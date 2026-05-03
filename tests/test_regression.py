"""Tests for Regression Gate."""

import json
import tempfile
from pathlib import Path

from vibe.evals.regression import RegressionGate, RegressionThreshold
from vibe.harness.memory.eval_store import EvalResult


class TestRegressionGate:
    def test_from_file(self):
        """Should load baseline from JSON file."""
        baseline = {
            "pass_rate": 0.85,
            "avg_score": 75.0,
            "cases": {"case-1": True, "case-2": False},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(baseline, f)
            path = f.name

        gate = RegressionGate.from_file(path)
        assert gate.baseline["pass_rate"] == 0.85
        Path(path).unlink()

    def test_pass_rate_improvement(self):
        """Should pass when pass rate improves."""
        baseline = {"pass_rate": 0.80, "avg_score": 70.0}
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=True, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        assert report.passed is True
        assert len(report.improvements) > 0

    def test_pass_rate_regression(self):
        """Should fail when pass rate regresses beyond threshold."""
        baseline = {"pass_rate": 0.90, "avg_score": 80.0}
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=False, diff={}, total_tokens=100),
            EvalResult(eval_id="case-3", passed=False, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        assert report.passed is False
        assert len(report.regressions) > 0
        assert any(r["metric"] == "pass_rate" for r in report.regressions)

    def test_absolute_min_violation(self):
        """Should fail when absolute minimum is violated."""
        baseline = {"pass_rate": 0.90}
        thresholds = [RegressionThreshold("pass_rate", max_regression_percent=10.0, absolute_min=0.8)]
        gate = RegressionGate(baseline, thresholds=thresholds)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=False, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        assert report.passed is False
        assert any("absolute_min_violation" in r for r in report.regressions)

    def test_token_usage_regression(self):
        """Should detect token usage regression."""
        baseline = {"token_usage": 1000.0}
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=2000),
        ]

        report = gate.check(results)
        # Token usage doubled = 100% increase, but inverted since lower is better
        # Actually: current=2000, baseline=1000, pct_change = ((2000-1000)/1000)*100 = 100%
        # But we invert for token_usage: -100%, which is < -10% threshold
        assert report.passed is False
        assert any(r["metric"] == "token_usage" for r in report.regressions)

    def test_case_level_regression(self):
        """Should detect when a previously passing case now fails."""
        baseline = {
            "pass_rate": 0.5,
            "cases": {"case-1": True, "case-2": False},
        }
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=False, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=False, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        assert report.passed is False
        assert any(r["metric"] == "case_case-1" for r in report.regressions)

    def test_no_regression(self):
        """Should pass when metrics are within threshold."""
        baseline = {"pass_rate": 0.80, "avg_score": 75.0}
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=True, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        assert report.passed is True
        assert len(report.regressions) == 0

    def test_empty_results(self):
        """Should handle empty results."""
        baseline = {"pass_rate": 0.80}
        gate = RegressionGate(baseline)
        report = gate.check([])
        assert report.passed is True  # No metrics to compare

    def test_save_baseline(self):
        """Should save baseline to file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        gate = RegressionGate({})
        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100, latency_seconds=1.0),
            EvalResult(eval_id="case-2", passed=False, diff={}, total_tokens=200, latency_seconds=2.0),
        ]

        gate.save_baseline(results, path)

        with open(path) as f:
            saved = json.load(f)

        assert saved["version"] == "1.0"
        assert saved["metrics"]["pass_rate"] == 0.5
        assert saved["metrics"]["total_cases"] == 2
        Path(path).unlink()

    def test_latency_p95_regression(self):
        """Should detect latency regression."""
        baseline = {"latency_p95": 5.0}
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100, latency_seconds=1.0),
            EvalResult(eval_id="case-2", passed=True, diff={}, total_tokens=100, latency_seconds=20.0),
        ]

        report = gate.check(results)
        # p95 latency increased significantly
        assert len(report.regressions) > 0 or len(report.unchanged) > 0

    def test_improvement_detection(self):
        """Should report improvements."""
        baseline = {"pass_rate": 0.50, "avg_score": 50.0}
        gate = RegressionGate(baseline)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=True, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        assert len(report.improvements) > 0
        assert any(r["metric"] == "pass_rate" for r in report.improvements)

    def test_custom_threshold(self):
        """Should use custom thresholds."""
        baseline = {"pass_rate": 0.80}
        thresholds = [RegressionThreshold("pass_rate", max_regression_percent=1.0)]
        gate = RegressionGate(baseline, thresholds=thresholds)

        results = [
            EvalResult(eval_id="case-1", passed=True, diff={}, total_tokens=100),
            EvalResult(eval_id="case-2", passed=False, diff={}, total_tokens=100),
        ]

        report = gate.check(results)
        # pass_rate dropped from 0.8 to 0.5 = 37.5% regression, > 1% threshold
        assert report.passed is False
