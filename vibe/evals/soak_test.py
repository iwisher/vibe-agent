"""Long-running soak test for vibe-agent evals.

Runs eval cases continuously in a loop for a configurable duration,
tracking metrics over time to detect degradation, memory leaks,
rate limiting, and drift.
"""

import asyncio
import json
import signal
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from vibe.evals.observability import Observability
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalCase, EvalStore


@dataclass
class SoakSnapshot:
    """A single snapshot of soak test metrics at a point in time."""

    timestamp: str
    loop_iteration: int
    case_id: str
    passed: bool
    latency_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tool_call_count: int
    turn_count: int
    rss_mb: float = 0.0
    error: str | None = None


@dataclass
class SoakReport:
    """Aggregated report from a soak test run."""

    model: str
    base_url: str
    duration_seconds: float
    total_iterations: int
    total_cases_run: int
    pass_count: int
    fail_count: int
    pass_rate: float
    avg_latency: float
    p50_latency: float
    p95_latency: float
    p99_latency: float
    avg_tokens_per_case: float
    tokens_per_second: float
    rss_start_mb: float
    rss_end_mb: float
    rss_delta_mb: float
    error_count: int
    unique_errors: dict[str, int]
    degradation_detected: bool
    snapshots: list[SoakSnapshot] = field(default_factory=list)


class SoakTestRunner:
    """Continuously runs eval cases in a loop for soak testing."""

    def __init__(
        self,
        query_loop_factory,
        eval_store: EvalStore,
        model: str,
        base_url: str,
        duration_minutes: float = 60.0,
        cases_per_minute: float = 6.0,
        output_dir: str | None = None,
        observability: Observability | None = None,
    ):
        self.query_loop_factory = query_loop_factory
        self.eval_store = eval_store
        self.model = model
        self.base_url = base_url
        self.duration_seconds = duration_minutes * 60
        self.target_interval = 60.0 / cases_per_minute
        self.output_dir = Path(output_dir or str(Path.home() / ".vibe" / "soak"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop = False
        self._snapshots: list[SoakSnapshot] = []
        self._current_loop = 0
        self._checkpoint_index = 0
        self.obs = observability

    def _setup_signal_handlers(self):
        def handle_sigint(signum, frame):
            print("\n[soak] Received SIGINT, stopping gracefully...")
            self._stop = True

        signal.signal(signal.SIGINT, handle_sigint)

    async def run(self, cases: list[EvalCase]) -> SoakReport:
        self._setup_signal_handlers()
        start_time = time.time()
        end_time = start_time + self.duration_seconds

        print(f"\n{'═' * 70}")
        print("  SOAK TEST STARTED")
        print(f"{'═' * 70}")
        print(f"  Model: {self.model}")
        print(f"  Base URL: {self.base_url}")
        print(f"  Duration: {self.duration_seconds / 60:.1f} minutes")
        print(f"  Cases: {len(cases)}")
        print(f"  Target interval: {self.target_interval:.1f}s between cases")
        print(f"  Output: {self.output_dir}")
        print(f"{'═' * 70}\n")

        case_idx = 0
        loop_iteration = 0

        while not self._stop and time.time() < end_time:
            loop_iteration += 1
            self._current_loop = loop_iteration
            case = cases[case_idx % len(cases)]
            case_idx += 1

            # Create fresh QueryLoop for each case to avoid state pollution
            query_loop = self.query_loop_factory()
            runner = EvalRunner(
                query_loop=query_loop,
                eval_store=self.eval_store,
                observability=self.obs,
            )

            snapshot_start = time.time()
            try:
                result = await runner.run_case(case)
                latency = time.time() - snapshot_start

                # Extract metrics from QueryLoop state
                turn_count = len(query_loop.messages)
                tool_call_count = sum(
                    1
                    for m in query_loop.messages
                    if m.role == "assistant" and m.tool_calls
                    for _ in (m.tool_calls or [])
                )

                total_tokens = result.total_tokens
                metrics = getattr(result, "metrics", None) or {}
                prompt_tokens = getattr(metrics, "prompt_tokens", 0) if hasattr(metrics, "prompt_tokens") else metrics.get("prompt_tokens", 0)
                completion_tokens = getattr(metrics, "completion_tokens", 0) if hasattr(metrics, "completion_tokens") else metrics.get("completion_tokens", 0)

                # Record observability metrics for this iteration
                if self.obs:
                    self.obs.histogram("soak_latency", latency, labels={"case_id": case.id})
                    self.obs.counter("soak_passed", 1.0 if result.passed else 0.0, labels={"case_id": case.id})
                    self.obs.gauge("soak_tokens", total_tokens, labels={"case_id": case.id})

                snapshot = SoakSnapshot(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    loop_iteration=loop_iteration,
                    case_id=case.id,
                    passed=result.passed,
                    latency_seconds=latency,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    tool_call_count=tool_call_count,
                    turn_count=turn_count,
                    error=None,
                )
            except Exception as e:
                latency = time.time() - snapshot_start
                snapshot = SoakSnapshot(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    loop_iteration=loop_iteration,
                    case_id=case.id,
                    passed=False,
                    latency_seconds=latency,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    tool_call_count=0,
                    turn_count=0,
                    error=str(e),
                )
            finally:
                # Cleanup LLM client regardless of success/failure
                try:
                    await query_loop.close()
                except Exception:
                    pass

            self._snapshots.append(snapshot)

            # Progress report every 10 cases
            if loop_iteration % 10 == 0:
                elapsed = time.time() - start_time
                remaining = max(0, end_time - time.time())
                recent = self._snapshots[-10:]
                recent_pass_rate = sum(1 for s in recent if s.passed) / len(recent)
                avg_lat = statistics.mean(s.latency_seconds for s in recent)
                print(
                    f"[soak] iter={loop_iteration:4d} | elapsed={elapsed/60:.1f}min | "
                    f"remaining={remaining/60:.1f}min | pass_rate(10)={recent_pass_rate:.0%} | "
                    f"avg_lat={avg_lat:.1f}s"
                )

                # Save incremental checkpoint
                self._save_checkpoint()

            # Throttle to target interval
            await asyncio.sleep(max(0, self.target_interval - latency))

        return self._generate_report(start_time, loop_iteration)

    def _save_checkpoint(self):
        checkpoint_path = self.output_dir / f"soak_checkpoint_{self.model.replace('/', '_')}.jsonl"
        with open(checkpoint_path, "a") as f:
            for s in self._snapshots[self._checkpoint_index:]:
                f.write(json.dumps({
                    "timestamp": s.timestamp,
                    "loop_iteration": s.loop_iteration,
                    "case_id": s.case_id,
                    "passed": s.passed,
                    "latency_seconds": s.latency_seconds,
                    "prompt_tokens": s.prompt_tokens,
                    "completion_tokens": s.completion_tokens,
                    "total_tokens": s.total_tokens,
                    "tool_call_count": s.tool_call_count,
                    "turn_count": s.turn_count,
                    "error": s.error,
                }) + "\n")
        self._checkpoint_index = len(self._snapshots)

    def _generate_report(self, start_time: float, total_iterations: int) -> SoakReport:
        elapsed = time.time() - start_time
        snapshots = self._snapshots

        if not snapshots:
            return SoakReport(
                model=self.model,
                base_url=self.base_url,
                duration_seconds=elapsed,
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
                snapshots=snapshots,
            )

        latencies = [s.latency_seconds for s in snapshots]
        latencies.sort()
        n = len(latencies)

        passes = [s for s in snapshots if s.passed]
        failures = [s for s in snapshots if not s.passed]
        errors = [s for s in snapshots if s.error]

        # Detect degradation: compare chronologically (first 20% iterations vs last 20%)
        degradation = False
        if n >= 20:
            # Use snapshots in chronological order (they are appended sequentially)
            first_count = max(1, n // 5)
            first_lats = [s.latency_seconds for s in snapshots[:first_count]]
            last_lats = [s.latency_seconds for s in snapshots[-first_count:]]
            if statistics.median(last_lats) > statistics.median(first_lats) * 1.5:
                degradation = True

        # Collect unique errors
        error_counts: dict[str, int] = {}
        for s in errors:
            key = s.error or "unknown"
            error_counts[key] = error_counts.get(key, 0) + 1

        rss_values = [s.rss_mb for s in snapshots]
        rss_start = rss_values[0] if rss_values else 0.0
        rss_end = rss_values[-1] if rss_values else 0.0

        report = SoakReport(
            model=self.model,
            base_url=self.base_url,
            duration_seconds=elapsed,
            total_iterations=total_iterations,
            total_cases_run=len(snapshots),
            pass_count=len(passes),
            fail_count=len(failures),
            pass_rate=len(passes) / len(snapshots),
            avg_latency=statistics.mean(latencies),
            p50_latency=latencies[n // 2] if n > 0 else 0,
            p95_latency=latencies[int(n * 0.95)] if n > 0 else 0,
            p99_latency=latencies[int(n * 0.99)] if n > 0 else 0,
            avg_tokens_per_case=statistics.mean(s.total_tokens for s in snapshots) if snapshots else 0.0,
            tokens_per_second=(sum(s.total_tokens for s in snapshots) / elapsed) if elapsed > 0 and snapshots else 0.0,
            rss_start_mb=rss_start,
            rss_end_mb=rss_end,
            rss_delta_mb=rss_end - rss_start,
            error_count=len(errors),
            unique_errors=error_counts,
            degradation_detected=degradation,
            snapshots=snapshots,
        )

        self._save_report(report)
        return report

    def _save_report(self, report: SoakReport):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_path = self.output_dir / f"soak_report_{self.model.replace('/', '_')}_{timestamp}.json"

        report_dict = {
            "model": report.model,
            "base_url": report.base_url,
            "duration_seconds": report.duration_seconds,
            "total_iterations": report.total_iterations,
            "total_cases_run": report.total_cases_run,
            "pass_count": report.pass_count,
            "fail_count": report.fail_count,
            "pass_rate": report.pass_rate,
            "avg_latency": report.avg_latency,
            "p50_latency": report.p50_latency,
            "p95_latency": report.p95_latency,
            "p99_latency": report.p99_latency,
            "avg_tokens_per_case": report.avg_tokens_per_case,
            "tokens_per_second": report.tokens_per_second,
            "rss_start_mb": report.rss_start_mb,
            "rss_end_mb": report.rss_end_mb,
            "rss_delta_mb": report.rss_delta_mb,
            "error_count": report.error_count,
            "unique_errors": report.unique_errors,
            "degradation_detected": report.degradation_detected,
            "time_series": [
                {
                    "iteration": s.loop_iteration,
                    "timestamp": s.timestamp,
                    "latency": s.latency_seconds,
                    "tokens": s.total_tokens,
                    "rss_mb": s.rss_mb,
                    "passed": s.passed,
                }
                for s in report.snapshots
            ],
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2)

        # Also save human-readable summary
        summary_path = self.output_dir / f"soak_summary_{self.model.replace('/', '_')}_{timestamp}.md"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(self._format_summary(report))

        print(f"\n[soak] Report saved: {report_path}")
        print(f"[soak] Summary saved: {summary_path}")

    @staticmethod
    def _format_summary(report: SoakReport) -> str:
        lines = [
            f"# Soak Test Report: {report.model}",
            "",
            f"- **Base URL**: {report.base_url}",
            f"- **Duration**: {report.duration_seconds / 60:.1f} minutes",
            f"- **Total Cases Run**: {report.total_cases_run}",
            f"- **Pass Rate**: {report.pass_rate:.1%}",
            f"- **Pass/Fail**: {report.pass_count}/{report.fail_count}",
            "",
            "## Latency",
            f"- **Average**: {report.avg_latency:.2f}s",
            f"- **P50**: {report.p50_latency:.2f}s",
            f"- **P95**: {report.p95_latency:.2f}s",
            f"- **P99**: {report.p99_latency:.2f}s",
            "",
            "## Memory",
            f"- **RSS Start**: {report.rss_start_mb:.1f} MB",
            f"- **RSS End**: {report.rss_end_mb:.1f} MB",
            f"- **RSS Delta**: {report.rss_delta_mb:+.1f} MB",
            "",
            "## Errors",
            f"- **Total Errors**: {report.error_count}",
            f"- **Degradation Detected**: {'⚠️ YES' if report.degradation_detected else '✅ No'}",
        ]
        if report.unique_errors:
            lines.append("")
            lines.append("### Unique Errors")
            for err, count in report.unique_errors.items():
                lines.append(f"- `{err}`: {count}x")
        return "\n".join(lines)


def print_report(report: SoakReport):
    """Print a soak test report to stdout."""
    print(f"\n{'═' * 70}")
    print("  SOAK TEST COMPLETE")
    print(f"{'═' * 70}")
    print(f"  Model: {report.model}")
    print(f"  Duration: {report.duration_seconds / 60:.1f} minutes")
    print(f"  Cases Run: {report.total_cases_run}")
    print(f"  Pass Rate: {report.pass_rate:.1%} ({report.pass_count}/{report.total_cases_run})")
    print(f"  Avg Latency: {report.avg_latency:.2f}s")
    print(f"  P50/P95/P99: {report.p50_latency:.2f}s / {report.p95_latency:.2f}s / {report.p99_latency:.2f}s")
    print(f"  Errors: {report.error_count}")
    print(f"  Degradation: {'⚠️ DETECTED' if report.degradation_detected else '✅ None'}")
    print(f"{'═' * 70}")
