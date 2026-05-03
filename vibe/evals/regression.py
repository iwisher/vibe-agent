"""Regression gate for eval runs.

Compares current eval results against a baseline scorecard and fails
if any metric regresses beyond the configured threshold.

Supports:
- Per-case pass/fail comparison
- Aggregate score comparison (pass rate, avg score)
- Token usage regression detection
- Latency regression detection
- Subsystem-level breakdowns
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from vibe.harness.memory.eval_store import EvalResult


@dataclass
class RegressionThreshold:
    """Threshold for a specific metric."""
    metric: str  # e.g., "pass_rate", "avg_score", "token_usage"
    max_regression_percent: float = 5.0  # Fail if regresses more than this %
    absolute_min: Optional[float] = None  # Hard floor (e.g., pass_rate >= 0.8)


DEFAULT_THRESHOLDS = [
    RegressionThreshold("pass_rate", max_regression_percent=5.0, absolute_min=0.7),
    RegressionThreshold("avg_score", max_regression_percent=5.0),
    RegressionThreshold("token_usage", max_regression_percent=10.0),
    RegressionThreshold("latency_p95", max_regression_percent=20.0),
]


@dataclass
class RegressionReport:
    """Report from a regression check."""
    passed: bool
    regressions: list[dict[str, Any]]  # List of regressed metrics
    improvements: list[dict[str, Any]]  # List of improved metrics
    unchanged: list[dict[str, Any]]
    baseline_summary: dict[str, Any]
    current_summary: dict[str, Any]


class RegressionGate:
    """Compares eval results against baseline and detects regressions.

    Usage:
        gate = RegressionGate.from_file("docs/baseline_scorecard.json")
        report = gate.check(current_results)
        if not report.passed:
            print("Regressions detected:", report.regressions)
    """

    def __init__(
        self,
        baseline: dict[str, Any],
        thresholds: Optional[list[RegressionThreshold]] = None,
    ):
        self.baseline = baseline
        self.thresholds = thresholds or list(DEFAULT_THRESHOLDS)

    @classmethod
    def from_file(cls, path: str | Path) -> "RegressionGate":
        """Load baseline from a JSON scorecard file."""
        with open(path) as f:
            baseline = json.load(f)
        return cls(baseline)

    def check(self, current_results: list[EvalResult]) -> RegressionReport:
        """Check current results against baseline.

        Args:
            current_results: List of EvalResult from current run

        Returns:
            RegressionReport with pass/fail and details
        """
        current_summary = self._summarize(current_results)
        baseline_summary = self.baseline

        regressions = []
        improvements = []
        unchanged = []
        passed = True

        for threshold in self.thresholds:
            metric = threshold.metric
            baseline_val = self._get_metric(baseline_summary, metric)
            current_val = self._get_metric(current_summary, metric)

            if baseline_val is None or current_val is None:
                continue

            # Calculate percent change
            if baseline_val == 0:
                pct_change = float("inf") if current_val > 0 else 0
            else:
                pct_change = ((current_val - baseline_val) / baseline_val) * 100

            # For metrics where lower is better (latency, tokens), invert
            if metric in ("token_usage", "latency_p95", "latency_avg"):
                pct_change = -pct_change

            entry = {
                "metric": metric,
                "baseline": round(baseline_val, 3),
                "current": round(current_val, 3),
                "change_percent": round(pct_change, 2),
            }

            # Check absolute minimum
            if threshold.absolute_min is not None and current_val < threshold.absolute_min:
                entry["absolute_min_violation"] = threshold.absolute_min
                regressions.append(entry)
                passed = False
                continue

            # Check regression threshold
            if pct_change < -threshold.max_regression_percent:
                regressions.append(entry)
                passed = False
            elif pct_change > threshold.max_regression_percent:
                improvements.append(entry)
            else:
                unchanged.append(entry)

        # Per-case regression check
        baseline_cases = baseline_summary.get("cases", {})
        current_cases = current_summary.get("cases", {})
        for case_id, baseline_passed in baseline_cases.items():
            current_passed = current_cases.get(case_id)
            if current_passed is False and baseline_passed is True:
                regressions.append({
                    "metric": f"case_{case_id}",
                    "baseline": "passed",
                    "current": "failed",
                    "change_percent": -100,
                })
                passed = False

        return RegressionReport(
            passed=passed,
            regressions=regressions,
            improvements=improvements,
            unchanged=unchanged,
            baseline_summary=baseline_summary,
            current_summary=current_summary,
        )

    def _summarize(self, results: list[EvalResult]) -> dict[str, Any]:
        """Summarize a list of eval results into metrics."""
        if not results:
            return {}

        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        pass_rate = passed_count / total if total > 0 else 0

        scores = [r.overall_score for r in results if hasattr(r, "overall_score") and r.overall_score is not None]
        avg_score = sum(scores) / len(scores) if scores else None

        tokens = [r.total_tokens for r in results if hasattr(r, "total_tokens")]
        avg_tokens = sum(tokens) / len(tokens) if tokens else 0

        latencies = [r.latency_seconds for r in results if hasattr(r, "latency_seconds")]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

        cases = {
            r.eval_id: r.passed
            for r in results
        }

        return {
            "pass_rate": pass_rate,
            "avg_score": avg_score,
            "token_usage": avg_tokens,
            "latency_avg": avg_latency,
            "latency_p95": p95_latency,
            "total_cases": total,
            "passed_cases": passed_count,
            "cases": cases,
        }

    def _get_metric(self, summary: dict[str, Any], metric: str) -> Optional[float]:
        """Extract a metric value from a summary dict."""
        val = summary.get(metric)
        if val is not None:
            return float(val)
        return None

    def save_baseline(self, results: list[EvalResult], path: str | Path) -> None:
        """Save current results as a new baseline scorecard."""
        summary = self._summarize(results)
        scorecard = {
            "version": "1.0",
            "generated_at": str(Path().absolute()),
            "metrics": summary,
            "thresholds": [
                {
                    "metric": t.metric,
                    "max_regression_percent": t.max_regression_percent,
                    "absolute_min": t.absolute_min,
                }
                for t in self.thresholds
            ],
        }
        with open(path, "w") as f:
            json.dump(scorecard, f, indent=2)
