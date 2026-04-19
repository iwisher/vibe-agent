"""Multi-model eval runner using the model registry.

Runs the same eval suite against multiple models and produces
comparative scorecards.
"""

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibe.core.query_loop import QueryLoop
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.harness.memory.eval_store import EvalStore, EvalCase, EvalResult
from vibe.evals.runner import EvalRunner
from vibe.evals.model_registry import ModelRegistry, ModelProfile
from vibe.evals.observability import Observability


@dataclass
class ModelRunResult:
    """Results from running evals against a single model."""

    model: str
    passed: int
    failed: int
    score: float
    avg_latency: float
    p95_latency: float
    total_tokens: int
    total_cases: int
    case_results: list[EvalResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Scorecard:
    """Comparative scorecard across multiple models."""

    timestamp: str
    eval_suite_version: str
    models: list[ModelRunResult]
    by_tag: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    best_overall: str | None = None
    best_by_tag: dict[str, str] = field(default_factory=dict)


class MultiModelRunner:
    """Runs evals against multiple models and generates scorecards."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        eval_store: EvalStore | None = None,
        observability: Observability | None = None,
    ):
        self.registry = registry or ModelRegistry()
        self.eval_store = eval_store
        self.obs = observability or Observability()

    def _create_query_loop(self, profile: ModelProfile) -> QueryLoop:
        """Create a fresh QueryLoop for a model profile."""
        return QueryLoopFactory.from_profile(profile)

    async def run_model(
        self,
        profile: ModelProfile,
        cases: list[EvalCase],
    ) -> ModelRunResult:
        """Run all eval cases against a single model."""
        print(f"\n{'─' * 70}")
        print(f"  ▶ MODEL: {profile.name} ({profile.model_id})")
        print(f"  Provider: {profile.provider} | Base: {profile.base_url}")
        print(f"{'─' * 70}")

        self.obs.counter("model_runs", labels={"model": profile.name})

        results: list[EvalResult] = []
        latencies: list[float] = []
        errors: list[str] = []
        total_tokens = 0

        for case in cases:
            with self.obs.span(
                "eval_case",
                attributes={"model": profile.name, "case": case.id},
            ):
                query_loop = None
                case_start = time.time()
                try:
                    query_loop = self._create_query_loop(profile)
                    runner = EvalRunner(
                        query_loop=query_loop,
                        eval_store=self.eval_store,
                    )
                    result = await runner.run_case(case)
                    latency = time.time() - case_start

                    results.append(result)
                    latencies.append(latency)
                    total_tokens += result.total_tokens

                    self.obs.histogram(
                        "eval_latency",
                        latency,
                        labels={"model": profile.name, "case": case.id},
                    )
                    self.obs.counter(
                        "eval_results",
                        labels={
                            "model": profile.name,
                            "case": case.id,
                            "passed": str(result.passed),
                        },
                    )

                    status = "✅" if result.passed else "❌"
                    print(f"  {status} {case.id} ({latency:.1f}s)")
                    if not result.passed:
                        for k, v in result.diff.items():
                            print(f"      → {k}: {v}")

                except Exception as e:
                    latency = time.time() - case_start
                    latencies.append(latency)
                    errors.append(str(e))
                    print(f"  ❌ {case.id} EXCEPTION: {e}")
                    self.obs.counter(
                        "eval_errors",
                        labels={"model": profile.name, "case": case.id},
                    )

                finally:
                    # Cleanup LLM client
                    if query_loop is not None:
                        try:
                            await query_loop.close()
                        except Exception:
                            pass

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        return ModelRunResult(
            model=profile.name,
            passed=passed,
            failed=failed,
            score=passed / len(cases) if cases else 0.0,
            avg_latency=statistics.mean(latencies) if latencies else 0.0,
            p95_latency=self._p95(latencies) if latencies else 0.0,
            total_tokens=total_tokens,
            total_cases=len(cases),
            case_results=results,
            errors=errors,
        )

    async def run_all(
        self,
        model_names: list[str],
        cases: list[EvalCase],
        parallel: bool = False,
    ) -> Scorecard:
        """Run evals against multiple models."""
        self.obs.reset()

        profiles = []
        for name in model_names:
            profile = self.registry.get(name)
            if not profile:
                print(f"[warn] Model '{name}' not found in registry, skipping")
                continue
            profiles.append(profile)

        if not profiles:
            raise ValueError("No valid models to run")

        print(f"\n{'═' * 70}")
        print(f"  MULTI-MODEL BENCHMARK")
        print(f"  Models: {', '.join(p.name for p in profiles)}")
        print(f"  Cases: {len(cases)}")
        print(f"  Parallel: {parallel}")
        print(f"{'═' * 70}")

        start_time = time.time()

        if parallel:
            raw_results = await asyncio.gather(
                *[self.run_model(p, cases) for p in profiles],
                return_exceptions=True,
            )
            model_results = []
            for r in raw_results:
                if isinstance(r, Exception):
                    print(f"[benchmark] Model failed: {r}")
                    self.obs.counter("benchmark_model_failure")
                else:
                    model_results.append(r)
        else:
            model_results = []
            for p in profiles:
                result = await self.run_model(p, cases)
                model_results.append(result)

        elapsed = time.time() - start_time

        # Determine best overall
        best_overall = None
        best_score = -1
        for r in model_results:
            if r.score > best_score:
                best_score = r.score
                best_overall = r.model

        # Aggregate by tag
        by_tag: dict[str, dict[str, dict[str, Any]]] = {}
        for r in model_results:
            for case_result in r.case_results:
                # Find case tags
                case = next((c for c in cases if c.id == case_result.eval_id), None)
                if not case:
                    continue
                for tag in case.tags:
                    if tag not in by_tag:
                        by_tag[tag] = {}
                    if r.model not in by_tag[tag]:
                        by_tag[tag][r.model] = {"passed": 0, "failed": 0}
                    if case_result.passed:
                        by_tag[tag][r.model]["passed"] += 1
                    else:
                        by_tag[tag][r.model]["failed"] += 1

        # Best per tag
        best_by_tag = {}
        for tag, model_scores in by_tag.items():
            best_model = max(
                model_scores.keys(),
                key=lambda m: model_scores[m]["passed"]
                / (model_scores[m]["passed"] + model_scores[m]["failed"]),
            )
            best_by_tag[tag] = best_model

        scorecard = Scorecard(
            timestamp=datetime.now(timezone.utc).isoformat(),
            eval_suite_version="unknown",
            models=model_results,
            by_tag=by_tag,
            best_overall=best_overall,
            best_by_tag=best_by_tag,
        )

        self._print_scorecard(scorecard, elapsed)
        return scorecard

    def _print_scorecard(self, scorecard: Scorecard, elapsed: float):
        print(f"\n{'═' * 70}")
        print("  SCORECARD")
        print(f"{'═' * 70}")
        print(f"  Total time: {elapsed:.1f}s")
        print(f"  Best overall: {scorecard.best_overall}")
        print()

        # Overall table
        print("  Overall Results:")
        print(f"  {'Model':<20} {'Pass':>6} {'Fail':>6} {'Score':>8} {'AvgLat':>10} {'P95Lat':>10}")
        print(f"  {'-' * 62}")
        for r in scorecard.models:
            print(
                f"  {r.model:<20} {r.passed:>6} {r.failed:>6} "
                f"{r.score:>7.1%} {r.avg_latency:>9.2f}s {r.p95_latency:>9.2f}s"
            )
        print()

        # Per-tag breakdown
        if scorecard.by_tag:
            print("  By Tag:")
            for tag, models in scorecard.by_tag.items():
                best = scorecard.best_by_tag.get(tag, "-")
                print(f"    [{tag}] best={best}")
                for model, scores in models.items():
                    total = scores["passed"] + scores["failed"]
                    pct = scores["passed"] / total if total else 0
                    print(f"      {model}: {scores['passed']}/{total} ({pct:.0%})")
            print()

        print(f"{'═' * 70}")

    def save_scorecard(self, scorecard: Scorecard, path: str | None = None) -> str:
        """Save scorecard to JSON and Markdown."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_dir = Path.home() / ".vibe" / "scorecards"
        output_dir.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = path or str(output_dir / f"scorecard_{timestamp}.json")
        data = {
            "timestamp": scorecard.timestamp,
            "eval_suite_version": scorecard.eval_suite_version,
            "best_overall": scorecard.best_overall,
            "best_by_tag": scorecard.best_by_tag,
            "models": [
                {
                    "model": r.model,
                    "passed": r.passed,
                    "failed": r.failed,
                    "score": r.score,
                    "avg_latency": r.avg_latency,
                    "p95_latency": r.p95_latency,
                    "total_tokens": r.total_tokens,
                    "total_cases": r.total_cases,
                    "errors": r.errors,
                }
                for r in scorecard.models
            ],
            "by_tag": scorecard.by_tag,
        }
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2)

        # Markdown
        md_path = str(output_dir / f"scorecard_{timestamp}.md")
        with open(md_path, "w") as f:
            f.write(self._format_markdown(scorecard))

        print(f"\n[scorecard] JSON: {json_path}")
        print(f"[scorecard] Markdown: {md_path}")
        return json_path

    def _format_markdown(self, scorecard: Scorecard) -> str:
        lines = [
            "# Multi-Model Scorecard",
            "",
            f"**Generated**: {scorecard.timestamp}",
            f"**Best Overall**: {scorecard.best_overall}",
            "",
            "## Overall Results",
            "",
            "| Model | Passed | Failed | Score | Avg Latency | P95 Latency |",
            "|-------|--------|--------|-------|-------------|-------------|",
        ]
        for r in scorecard.models:
            lines.append(
                f"| {r.model} | {r.passed} | {r.failed} | {r.score:.1%} | "
                f"{r.avg_latency:.2f}s | {r.p95_latency:.2f}s |"
            )

        if scorecard.by_tag:
            lines.extend(["", "## By Tag", ""])
            for tag, models in scorecard.by_tag.items():
                best = scorecard.best_by_tag.get(tag, "-")
                lines.append(f"### {tag} (best: {best})")
                lines.append("")
                lines.append("| Model | Passed | Failed | Score |")
                lines.append("|-------|--------|--------|-------|")
                for model, scores in models.items():
                    total = scores["passed"] + scores["failed"]
                    pct = scores["passed"] / total if total else 0
                    lines.append(f"| {model} | {scores['passed']} | {scores['failed']} | {pct:.1%} |")
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * 0.95)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]
