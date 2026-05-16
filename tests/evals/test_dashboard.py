"""Tests for CI eval dashboard."""

import time

import pytest

from vibe.evals.dashboard import EvalDashboard, EvalRunSummary, generate_from_pytest


class TestEvalRunSummary:
    def test_pass_rate(self):
        s = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=10,
            passed=8, failed=2, skipped=0, duration_seconds=5.0
        )
        assert s.pass_rate == 0.8

    def test_pass_rate_zero(self):
        s = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=0,
            passed=0, failed=0, skipped=0, duration_seconds=0.0
        )
        assert s.pass_rate == 0.0

    def test_status_pass(self):
        s = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=10,
            passed=10, failed=0, skipped=0, duration_seconds=5.0
        )
        assert s.status == "PASS"

    def test_status_warn(self):
        s = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=100,
            passed=96, failed=4, skipped=0, duration_seconds=5.0
        )
        assert s.status == "WARN"

    def test_status_fail(self):
        s = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=10,
            passed=8, failed=2, skipped=0, duration_seconds=5.0
        )
        assert s.status == "FAIL"

    def test_to_dict(self):
        s = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=10,
            passed=8, failed=2, skipped=0, duration_seconds=5.0
        )
        d = s.to_dict()
        assert d["run_id"] == "test-1"
        assert d["pass_rate"] == 0.8
        assert d["status"] == "FAIL"


class TestEvalDashboard:
    def test_generate_creates_file(self, tmp_path):
        dashboard = EvalDashboard(output_dir=str(tmp_path))
        summary = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=10,
            passed=8, failed=2, skipped=0, duration_seconds=5.0
        )
        path = dashboard.generate([summary])
        assert path.endswith(".html")
        import pathlib
        assert pathlib.Path(path).exists()

    def test_generate_empty_summaries(self, tmp_path):
        dashboard = EvalDashboard(output_dir=str(tmp_path))
        path = dashboard.generate([])
        assert path.endswith(".html")

    def test_html_contains_data(self, tmp_path):
        dashboard = EvalDashboard(output_dir=str(tmp_path))
        summary = EvalRunSummary(
            run_id="test-1", timestamp=time.time(), total_tests=10,
            passed=8, failed=2, skipped=0, duration_seconds=5.0
        )
        path = dashboard.generate([summary])
        content = open(path).read()
        assert "FAIL" in content
        assert "10" in content
        assert "80.0%" in content


class TestGenerateFromPytest:
    def test_from_pytest_results(self, tmp_path):
        pytest_results = {
            "summary": {"total": 20, "passed": 18, "failed": 2, "skipped": 0},
            "duration": 10.5,
            "environment": {"Python": "3.11"},
        }
        path = generate_from_pytest(pytest_results, output_dir=str(tmp_path))
        assert path.endswith(".html")
        content = open(path).read()
        assert "20" in content
        assert "90.0%" in content
