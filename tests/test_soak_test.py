"""Tests for vibe.evals.soak_test module."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from vibe.evals.soak_test import SoakReport, SoakSnapshot, SoakTestRunner, print_report


class TestSoakSnapshot:
    """Tests for SoakSnapshot dataclass."""

    def test_creation_with_all_fields(self):
        snapshot = SoakSnapshot(
            timestamp="2024-01-01T00:00:00",
            loop_iteration=1,
            case_id="case_1",
            passed=True,
            latency_seconds=1.5,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            tool_call_count=2,
            turn_count=3,
        )
        assert snapshot.timestamp == "2024-01-01T00:00:00"
        assert snapshot.loop_iteration == 1
        assert snapshot.case_id == "case_1"
        assert snapshot.passed is True
        assert snapshot.latency_seconds == 1.5
        assert snapshot.prompt_tokens == 10
        assert snapshot.completion_tokens == 20
        assert snapshot.total_tokens == 30
        assert snapshot.tool_call_count == 2
        assert snapshot.turn_count == 3
        assert snapshot.error is None

    def test_default_error_is_none(self):
        snapshot = SoakSnapshot(
            timestamp="2024-01-01T00:00:00",
            loop_iteration=1,
            case_id="case_1",
            passed=False,
            latency_seconds=0.5,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            tool_call_count=0,
            turn_count=0,
        )
        assert snapshot.error is None

    def test_explicit_error(self):
        snapshot = SoakSnapshot(
            timestamp="2024-01-01T00:00:00",
            loop_iteration=1,
            case_id="case_1",
            passed=False,
            latency_seconds=0.5,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            tool_call_count=0,
            turn_count=0,
            error="Connection timeout",
        )
        assert snapshot.error == "Connection timeout"


class TestSoakReport:
    """Tests for SoakReport dataclass."""

    def test_creation_with_defaults(self):
        report = SoakReport(
            model="gpt-4",
            base_url="http://localhost:11434",
            duration_seconds=3600.0,
            total_iterations=100,
            total_cases_run=100,
            pass_count=95,
            fail_count=5,
            pass_rate=0.95,
            avg_latency=1.2,
            p50_latency=1.0,
            p95_latency=2.5,
            p99_latency=3.0,
            avg_tokens_per_case=150.0,
            tokens_per_second=125.0,
            rss_start_mb=100.0,
            rss_end_mb=105.0,
            rss_delta_mb=5.0,
            error_count=2,
            unique_errors={"timeout": 2},
            degradation_detected=False,
        )
        assert report.model == "gpt-4"
        assert report.base_url == "http://localhost:11434"
        assert report.duration_seconds == 3600.0
        assert report.total_iterations == 100
        assert report.total_cases_run == 100
        assert report.pass_count == 95
        assert report.fail_count == 5
        assert report.pass_rate == 0.95
        assert report.avg_latency == 1.2
        assert report.p50_latency == 1.0
        assert report.p95_latency == 2.5
        assert report.p99_latency == 3.0
        assert report.avg_tokens_per_case == 150.0
        assert report.tokens_per_second == 125.0
        assert report.error_count == 2
        assert report.unique_errors == {"timeout": 2}
        assert report.degradation_detected is False
        assert report.snapshots == []

    def test_snapshots_default_factory(self):
        """Ensure snapshots defaults to a new empty list per instance."""
        report1 = SoakReport(
            model="m1",
            base_url="http://a",
            duration_seconds=1.0,
            total_iterations=0,
            total_cases_run=0,
            pass_count=0,
            fail_count=0,
            pass_rate=0.0,
            avg_latency=0.0,
            p50_latency=0.0,
            p95_latency=0.0,
            p99_latency=0.0,
            avg_tokens_per_case=0.0,
            tokens_per_second=0.0,
            rss_start_mb=0.0,
            rss_end_mb=0.0,
            rss_delta_mb=0.0,
            error_count=0,
            unique_errors={},
            degradation_detected=False,
        )
        report2 = SoakReport(
            model="m2",
            base_url="http://b",
            duration_seconds=1.0,
            total_iterations=0,
            total_cases_run=0,
            pass_count=0,
            fail_count=0,
            pass_rate=0.0,
            avg_latency=0.0,
            p50_latency=0.0,
            p95_latency=0.0,
            p99_latency=0.0,
            avg_tokens_per_case=0.0,
            tokens_per_second=0.0,
            rss_start_mb=0.0,
            rss_end_mb=0.0,
            rss_delta_mb=0.0,
            error_count=0,
            unique_errors={},
            degradation_detected=False,
        )
        report1.snapshots.append(SoakSnapshot(
            timestamp="t", loop_iteration=1, case_id="c", passed=True,
            latency_seconds=1.0, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, tool_call_count=0, turn_count=1,
        ))
        assert len(report1.snapshots) == 1
        assert len(report2.snapshots) == 0


class TestSoakTestRunnerInit:
    """Tests for SoakTestRunner initialization."""

    def test_output_dir_created(self, tmp_path):
        output_dir = tmp_path / "soak_output"
        assert not output_dir.exists()

        mock_factory = MagicMock()
        mock_store = MagicMock()

        runner = SoakTestRunner(
            query_loop_factory=mock_factory,
            eval_store=mock_store,
            model="test-model",
            base_url="http://test",
            output_dir=str(output_dir),
        )

        assert runner.output_dir == output_dir
        assert output_dir.exists()
        assert output_dir.is_dir()

    def test_default_output_dir_uses_home(self):
        mock_factory = MagicMock()
        mock_store = MagicMock()

        runner = SoakTestRunner(
            query_loop_factory=mock_factory,
            eval_store=mock_store,
            model="test-model",
            base_url="http://test",
        )

        assert runner.output_dir.name == "soak"
        assert runner.output_dir.parent.name == ".vibe"

    def test_checkpoint_index_starts_at_zero(self, tmp_path):
        mock_factory = MagicMock()
        mock_store = MagicMock()

        runner = SoakTestRunner(
            query_loop_factory=mock_factory,
            eval_store=mock_store,
            model="test-model",
            base_url="http://test",
            output_dir=str(tmp_path),
        )

        assert runner._checkpoint_index == 0
        assert runner._snapshots == []
        assert runner._current_loop == 0

    def test_duration_and_interval_conversion(self, tmp_path):
        mock_factory = MagicMock()
        mock_store = MagicMock()

        runner = SoakTestRunner(
            query_loop_factory=mock_factory,
            eval_store=mock_store,
            model="test-model",
            base_url="http://test",
            duration_minutes=30.0,
            cases_per_minute=12.0,
            output_dir=str(tmp_path),
        )

        assert runner.duration_seconds == 30.0 * 60
        assert runner.target_interval == 60.0 / 12.0


class TestSaveCheckpoint:
    """Tests for SoakTestRunner._save_checkpoint."""

    @pytest.fixture
    def runner(self, tmp_path):
        mock_factory = MagicMock()
        mock_store = MagicMock()
        return SoakTestRunner(
            query_loop_factory=mock_factory,
            eval_store=mock_store,
            model="test/model",
            base_url="http://test",
            output_dir=str(tmp_path),
        )

    def test_writes_jsonl_in_append_mode(self, runner, tmp_path):
        snapshot = SoakSnapshot(
            timestamp="2024-01-01T00:00:00",
            loop_iteration=1,
            case_id="case_1",
            passed=True,
            latency_seconds=1.0,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            tool_call_count=2,
            turn_count=3,
        )
        runner._snapshots.append(snapshot)
        runner._save_checkpoint()

        checkpoint_path = tmp_path / "soak_checkpoint_test_model.jsonl"
        assert checkpoint_path.exists()

        lines = checkpoint_path.read_text().strip().split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["timestamp"] == "2024-01-01T00:00:00"
        assert record["loop_iteration"] == 1
        assert record["case_id"] == "case_1"
        assert record["passed"] is True
        assert record["latency_seconds"] == 1.0
        assert record["prompt_tokens"] == 10
        assert record["completion_tokens"] == 20
        assert record["total_tokens"] == 30
        assert record["tool_call_count"] == 2
        assert record["turn_count"] == 3
        assert record["error"] is None

    def test_only_writes_new_snapshots(self, runner, tmp_path):
        snapshot1 = SoakSnapshot(
            timestamp="2024-01-01T00:00:00",
            loop_iteration=1,
            case_id="case_1",
            passed=True,
            latency_seconds=1.0,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            tool_call_count=0,
            turn_count=1,
        )
        snapshot2 = SoakSnapshot(
            timestamp="2024-01-01T00:00:01",
            loop_iteration=2,
            case_id="case_2",
            passed=False,
            latency_seconds=2.0,
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
            tool_call_count=1,
            turn_count=2,
        )

        runner._snapshots.append(snapshot1)
        runner._save_checkpoint()
        assert runner._checkpoint_index == 1

        runner._snapshots.append(snapshot2)
        runner._save_checkpoint()
        assert runner._checkpoint_index == 2

        checkpoint_path = tmp_path / "soak_checkpoint_test_model.jsonl"
        lines = checkpoint_path.read_text().strip().split("\n")
        assert len(lines) == 2

        records = [json.loads(line) for line in lines]
        assert records[0]["loop_iteration"] == 1
        assert records[1]["loop_iteration"] == 2

    def test_multiple_calls_append_correctly(self, runner, tmp_path):
        for i in range(3):
            runner._snapshots.append(SoakSnapshot(
                timestamp=f"2024-01-01T00:00:0{i}",
                loop_iteration=i + 1,
                case_id=f"case_{i}",
                passed=True,
                latency_seconds=float(i),
                prompt_tokens=i,
                completion_tokens=i,
                total_tokens=i * 2,
                tool_call_count=0,
                turn_count=1,
            ))
            runner._save_checkpoint()

        checkpoint_path = tmp_path / "soak_checkpoint_test_model.jsonl"
        lines = checkpoint_path.read_text().strip().split("\n")
        assert len(lines) == 3


class TestGenerateReport:
    """Tests for SoakTestRunner._generate_report."""

    @pytest.fixture
    def runner(self, tmp_path):
        mock_factory = MagicMock()
        mock_store = MagicMock()
        return SoakTestRunner(
            query_loop_factory=mock_factory,
            eval_store=mock_store,
            model="test-model",
            base_url="http://test",
            output_dir=str(tmp_path),
        )

    def test_empty_snapshots_returns_zeroed_report(self, runner):
        with patch("time.time", return_value=1000.0):
            report = runner._generate_report(start_time=1000.0, total_iterations=0)

        assert report.model == "test-model"
        assert report.base_url == "http://test"
        assert report.duration_seconds == 0.0
        assert report.total_iterations == 0
        assert report.total_cases_run == 0
        assert report.pass_count == 0
        assert report.fail_count == 0
        assert report.pass_rate == 0.0
        assert report.avg_latency == 0.0
        assert report.p50_latency == 0.0
        assert report.p95_latency == 0.0
        assert report.p99_latency == 0.0
        assert report.avg_tokens_per_case == 0.0
        assert report.tokens_per_second == 0.0
        assert report.error_count == 0
        assert report.unique_errors == {}
        assert report.degradation_detected is False
        assert report.rss_start_mb == 0.0
        assert report.rss_end_mb == 0.0
        assert report.rss_delta_mb == 0.0
        assert report.snapshots == []

    def test_computes_correct_metrics(self, runner):
        snapshots = [
            SoakSnapshot(
                timestamp="2024-01-01T00:00:00",
                loop_iteration=1,
                case_id="case_a",
                passed=True,
                latency_seconds=1.0,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                tool_call_count=0,
                turn_count=1,
            ),
            SoakSnapshot(
                timestamp="2024-01-01T00:00:01",
                loop_iteration=2,
                case_id="case_b",
                passed=True,
                latency_seconds=2.0,
                prompt_tokens=20,
                completion_tokens=30,
                total_tokens=50,
                tool_call_count=1,
                turn_count=2,
            ),
            SoakSnapshot(
                timestamp="2024-01-01T00:00:02",
                loop_iteration=3,
                case_id="case_c",
                passed=False,
                latency_seconds=3.0,
                prompt_tokens=5,
                completion_tokens=5,
                total_tokens=10,
                tool_call_count=0,
                turn_count=1,
                error="boom",
            ),
        ]
        runner._snapshots.extend(snapshots)

        with patch("time.time", return_value=1100.0):
            report = runner._generate_report(start_time=1000.0, total_iterations=3)

        assert report.duration_seconds == 100.0
        assert report.total_iterations == 3
        assert report.total_cases_run == 3
        assert report.pass_count == 2
        assert report.fail_count == 1
        assert report.pass_rate == pytest.approx(2 / 3)
        assert report.avg_latency == pytest.approx((1.0 + 2.0 + 3.0) / 3)
        # sorted latencies: [1.0, 2.0, 3.0]
        assert report.p50_latency == 2.0  # index 3 // 2 = 1 -> 2.0
        assert report.p95_latency == 3.0  # index int(3 * 0.95) = 2 -> 3.0
        assert report.p99_latency == 3.0  # index int(3 * 0.99) = 2 -> 3.0
        assert report.avg_tokens_per_case == pytest.approx((30 + 50 + 10) / 3)
        assert report.tokens_per_second == pytest.approx(90 / 100.0)
        assert report.error_count == 1
        assert report.unique_errors == {"boom": 1}
        assert report.degradation_detected is False

    def test_degradation_detected_when_latency_increases(self, runner):
        """Degradation: median(last 20%) > 1.5 * median(first 20%)."""
        # Need at least 20 snapshots to trigger degradation check
        for i in range(20):
            # First 4 iterations: low latency (first 20%)
            # Last 4 iterations: high latency (last 20%)
            latency = 1.0 if i < 4 else (10.0 if i >= 16 else 2.0)
            runner._snapshots.append(SoakSnapshot(
                timestamp=f"2024-01-01T00:00:{i:02d}",
                loop_iteration=i + 1,
                case_id=f"case_{i}",
                passed=True,
                latency_seconds=latency,
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                tool_call_count=0,
                turn_count=1,
            ))

        with patch("time.time", return_value=1100.0):
            report = runner._generate_report(start_time=1000.0, total_iterations=20)

        assert report.degradation_detected is True

    def test_no_degradation_when_latency_stable(self, runner):
        for i in range(20):
            runner._snapshots.append(SoakSnapshot(
                timestamp=f"2024-01-01T00:00:{i:02d}",
                loop_iteration=i + 1,
                case_id=f"case_{i}",
                passed=True,
                latency_seconds=2.0,
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                tool_call_count=0,
                turn_count=1,
            ))

        with patch("time.time", return_value=1100.0):
            report = runner._generate_report(start_time=1000.0, total_iterations=20)

        assert report.degradation_detected is False

    def test_report_saves_to_disk(self, runner, tmp_path):
        runner._snapshots.append(SoakSnapshot(
            timestamp="2024-01-01T00:00:00",
            loop_iteration=1,
            case_id="case_1",
            passed=True,
            latency_seconds=1.0,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            tool_call_count=0,
            turn_count=1,
        ))

        with patch("time.time", return_value=1100.0):
            with patch("vibe.evals.soak_test.datetime") as mock_dt:
                mock_dt.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
                mock_dt.strftime = datetime.strftime
                runner._generate_report(start_time=1000.0, total_iterations=1)

        report_files = list(tmp_path.glob("soak_report_test-model_*.json"))
        summary_files = list(tmp_path.glob("soak_summary_test-model_*.md"))
        assert len(report_files) == 1
        assert len(summary_files) == 1

        report_data = json.loads(report_files[0].read_text())
        assert report_data["model"] == "test-model"
        assert report_data["pass_count"] == 1
        assert report_data["degradation_detected"] is False


class TestPrintReport:
    """Tests for print_report function."""

    def test_outputs_expected_sections(self, capsys):
        report = SoakReport(
            model="my-model",
            base_url="http://localhost:11434",
            duration_seconds=3661.0,
            total_iterations=100,
            total_cases_run=100,
            pass_count=87,
            fail_count=13,
            pass_rate=0.87,
            avg_latency=1.234,
            p50_latency=1.0,
            p95_latency=2.5,
            p99_latency=3.7,
            avg_tokens_per_case=150.0,
            tokens_per_second=125.0,
            rss_start_mb=100.0,
            rss_end_mb=110.0,
            rss_delta_mb=10.0,
            error_count=5,
            unique_errors={"timeout": 3, "rate_limit": 2},
            degradation_detected=True,
        )

        print_report(report)

        captured = capsys.readouterr().out
        assert "SOAK TEST COMPLETE" in captured
        assert "Model: my-model" in captured
        assert "Duration: 61.0 minutes" in captured
        assert "Cases Run: 100" in captured
        assert "Pass Rate: 87.0% (87/100)" in captured
        assert "Avg Latency: 1.23s" in captured
        assert "P50/P95/P99: 1.00s / 2.50s / 3.70s" in captured
        assert "Errors: 5" in captured
        assert "Degradation: ⚠️ DETECTED" in captured

    def test_outputs_no_degradation(self, capsys):
        report = SoakReport(
            model="stable-model",
            base_url="http://test",
            duration_seconds=60.0,
            total_iterations=10,
            total_cases_run=10,
            pass_count=10,
            fail_count=0,
            pass_rate=1.0,
            avg_latency=0.5,
            p50_latency=0.5,
            p95_latency=0.9,
            p99_latency=1.0,
            avg_tokens_per_case=100.0,
            tokens_per_second=200.0,
            rss_start_mb=50.0,
            rss_end_mb=52.0,
            rss_delta_mb=2.0,
            error_count=0,
            unique_errors={},
            degradation_detected=False,
        )

        print_report(report)

        captured = capsys.readouterr().out
        assert "Degradation: ✅ None" in captured
        assert "Pass Rate: 100.0% (10/10)" in captured
