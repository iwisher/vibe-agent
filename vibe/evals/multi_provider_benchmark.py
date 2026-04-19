"""Multi-provider benchmarking for vibe-agent evals.

Runs the same eval suite across multiple models/providers and generates
a comparative scorecard.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from vibe.core.config import VibeConfig
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.evals.model_registry import ModelProfile, ModelRegistry
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalCase, EvalResult


@dataclass
class ModelScore:
    """Score for a single model across eval cases."""

    model_name: str
    provider: str
    passed: int = 0
    failed: int = 0
    total_tokens: int = 0
    total_time_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    @property
    def total_cases(self) -> int:
        return self.passed + self.failed

    @property
    def pass_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed / self.total_cases

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model_name,
            "provider": self.provider,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "total_tokens": self.total_tokens,
            "avg_time_ms": round(self.total_time_ms / max(self.total_cases, 1), 2),
            "errors": self.errors[:5],  # Cap errors
        }


@dataclass
class BenchmarkScorecard:
    """Comparative scorecard across multiple models."""

    models: Dict[str, ModelScore] = field(default_factory=dict)
    timestamp: str = ""
    duration_seconds: float = 0.0

    def add_result(self, model_name: str, result: EvalResult, provider: str = "unknown") -> None:
        if model_name not in self.models:
            self.models[model_name] = ModelScore(model_name=model_name, provider=provider)
        score = self.models[model_name]
        if result.passed:
            score.passed += 1
        else:
            score.failed += 1
        score.total_tokens += result.total_tokens
        score.total_time_ms += getattr(result, "latency_seconds", 0.0) * 1000
        if not result.passed and result.diff and "reason" in result.diff:
            if len(score.errors) < 5:
                score.errors.append(str(result.diff["reason"]))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "duration_seconds": round(self.duration_seconds, 2),
            "models": [s.to_dict() for s in self.models.values()],
        }

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("MULTI-PROVIDER BENCHMARK SCORECARD")
        print("=" * 60)
        for score in sorted(self.models.values(), key=lambda s: s.pass_rate, reverse=True):
            status = "PASS" if score.pass_rate >= 0.8 else "WARN" if score.pass_rate >= 0.5 else "FAIL"
            print(f"  [{status}] {score.model_name:20s} ({score.provider:10s})  "
                  f"{score.passed}/{score.total_cases}  {score.pass_rate*100:5.1f}%  "
                  f"{score.total_tokens:,} tokens")
        print("=" * 60)


class MultiProviderBenchmark:
    """Runs evals across multiple models and produces a scorecard."""

    def __init__(
        self,
        config: VibeConfig,
        model_registry: ModelRegistry,
        cases: List[EvalCase],
        max_concurrency: int = 2,
    ):
        self.config = config
        self.model_registry = model_registry
        self.cases = cases
        self.max_concurrency = max_concurrency

    async def run(self, model_names: Optional[List[str]] = None) -> BenchmarkScorecard:
        """Run benchmarks across specified models (or all registered models)."""
        targets = model_names or self.model_registry.list_models()
        scorecard = BenchmarkScorecard(timestamp=time.strftime("%Y-%m-%d %H:%M:%S"))
        start = time.time()

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_model(model_name: str) -> None:
            async with semaphore:
                profile = self.model_registry.get(model_name)
                if profile is None:
                    scorecard.models[model_name] = ModelScore(
                        model_name=model_name, provider="missing", errors=["Profile not found"]
                    )
                    return

                try:
                    factory = QueryLoopFactory.from_profile(profile, config=self.config)
                    query_loop = factory.create()
                    runner = EvalRunner(
                        query_loop=query_loop,
                        max_concurrency=1,
                    )
                    results = await runner.run_all(self.cases)
                    for result in results:
                        scorecard.add_result(model_name, result, provider=profile.provider)
                except Exception as exc:
                    scorecard.models[model_name] = ModelScore(
                        model_name=model_name,
                        provider=profile.provider,
                        errors=[str(exc)],
                    )

        await asyncio.gather(*(_run_model(name) for name in targets))
        scorecard.duration_seconds = time.time() - start
        return scorecard
