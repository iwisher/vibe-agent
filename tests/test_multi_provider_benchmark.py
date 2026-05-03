"""Tests for multi-provider benchmarking."""


from vibe.evals.multi_provider_benchmark import (
    BenchmarkScorecard,
    ModelScore,
    MultiProviderBenchmark,
)
from vibe.harness.memory.eval_store import EvalResult


class TestModelScore:
    def test_pass_rate(self):
        score = ModelScore(model_name="test", provider="ollama", passed=8, failed=2)
        assert score.total_cases == 10
        assert score.pass_rate == 0.8

    def test_to_dict(self):
        score = ModelScore(model_name="test", provider="ollama", passed=5, failed=5, total_tokens=1000)
        d = score.to_dict()
        assert d["model"] == "test"
        assert d["provider"] == "ollama"
        assert d["pass_rate"] == 0.5
        assert d["total_tokens"] == 1000


class TestBenchmarkScorecard:
    def test_add_result_pass(self):
        card = BenchmarkScorecard()
        result = EvalResult(eval_id="e1", passed=True, diff={}, total_tokens=50)
        card.add_result("llama3.2", result, provider="ollama")
        assert card.models["llama3.2"].passed == 1
        assert card.models["llama3.2"].failed == 0

    def test_add_result_fail(self):
        card = BenchmarkScorecard()
        result = EvalResult(eval_id="e1", passed=False, diff={"reason": "timeout"}, total_tokens=10)
        card.add_result("kimi", result, provider="kimi")
        assert card.models["kimi"].failed == 1
        assert "timeout" in card.models["kimi"].errors

    def test_to_dict(self):
        card = BenchmarkScorecard()
        card.add_result("m1", EvalResult(eval_id="e1", passed=True, diff={}, total_tokens=10), "p1")
        d = card.to_dict()
        assert len(d["models"]) == 1
        assert d["models"][0]["model"] == "m1"

    def test_print_summary(self, capsys):
        card = BenchmarkScorecard()
        card.add_result("m1", EvalResult(eval_id="e1", passed=True, diff={}, total_tokens=10), "p1")
        card.add_result("m1", EvalResult(eval_id="e2", passed=False, diff={}, total_tokens=5), "p1")
        card.print_summary()
        captured = capsys.readouterr()
        assert "MULTI-PROVIDER BENCHMARK SCORECARD" in captured.out
        assert "m1" in captured.out


class TestMultiProviderBenchmark:
    async def test_run_with_missing_model(self):
        from vibe.core.config import VibeConfig
        from vibe.evals.model_registry import ModelRegistry

        config = VibeConfig.load(auto_create=False)
        registry = ModelRegistry()
        benchmark = MultiProviderBenchmark(config, registry, cases=[], max_concurrency=1)
        scorecard = await benchmark.run(model_names=["missing-model"])
        assert "missing-model" in scorecard.models
        assert scorecard.models["missing-model"].errors == ["Profile not found"]
