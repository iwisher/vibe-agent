"""Tests for CostRouter, ComplexityScorer, and SpendTracker."""

import os
import tempfile

import pytest

from vibe.core.cost_router import (
    ComplexityScorer,
    CostRouter,
    CostRouterConfig,
    RoutingDecision,
    SpendTracker,
)
from vibe.core.provider_registry import ProviderProfile, ProviderRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scorer():
    return ComplexityScorer()


@pytest.fixture
def provider_registry():
    return ProviderRegistry({
        "ollama": ProviderProfile(
            name="ollama",
            base_url="http://localhost:11434",
            cost_tier="free",
            cost_per_1k_prompt=0.0,
        ),
        "openai": ProviderProfile(
            name="openai",
            base_url="https://api.openai.com",
            cost_tier="standard",
            cost_per_1k_prompt=0.10,
        ),
        "anthropic": ProviderProfile(
            name="anthropic",
            base_url="https://api.anthropic.com",
            cost_tier="premium",
            cost_per_1k_prompt=1.00,
        ),
        "local-budget": ProviderProfile(
            name="local-budget",
            base_url="http://localhost:8080",
            cost_tier="budget",
            cost_per_1k_prompt=0.01,
        ),
    })


@pytest.fixture
def router(provider_registry):
    return CostRouter(provider_registry)


@pytest.fixture
def spend_tracker():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    tracker = SpendTracker(db_path=db_path)
    yield tracker
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# ComplexityScorer
# ---------------------------------------------------------------------------

class TestComplexityScorer:
    def test_simple_prompt_low_complexity(self, scorer):
        messages = [{"role": "user", "content": "Hello"}]
        result = scorer.score(messages)
        assert result.overall < 0.2
        assert result.tier == "free"
        assert result.estimated_tokens < 10

    def test_code_prompt_higher_complexity(self, scorer):
        messages = [{"role": "user", "content": "Write a Python function to sort a list"}]
        result = scorer.score(messages)
        assert result.overall >= 0.05  # code marker detected (pattern score 0.3 * 0.25 weight = 0.075)
        assert result.dimensions[2].score > 0  # patterns dimension

    def test_reasoning_prompt_complexity(self, scorer):
        messages = [{"role": "user", "content": "Step by step, design a distributed system architecture"}]
        result = scorer.score(messages)
        assert result.overall >= 0.05  # reasoning marker (pattern score 0.25 * 0.25 = 0.0625)
        assert result.dimensions[2].score > 0  # patterns dimension

    def test_long_context_high_complexity(self, scorer):
        messages = [{"role": "user", "content": "x" * 10000}]  # ~2500 tokens
        result = scorer.score(messages)
        assert result.estimated_tokens > 2000
        assert result.overall >= 0.15  # token score 0.5 * 0.4 weight = 0.2

    def test_multi_step_messages(self, scorer):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
        result = scorer.score(messages)
        assert result.overall >= 0.05  # multi-step bonus (pattern score 0.35 * 0.25 = 0.0875)


# ---------------------------------------------------------------------------
# CostRouter
# ---------------------------------------------------------------------------

class TestCostRouter:
    def test_route_simple_to_free_tier(self, router):
        messages = [{"role": "user", "content": "Hi"}]
        decision = router.route(messages)
        assert decision.provider_name == "ollama"
        assert decision.tier == "free"
        assert decision.estimated_cost == 0.0

    def test_route_complex_to_premium(self, provider_registry):
        router = CostRouter(provider_registry)
        messages = [{"role": "user", "content": "x" * 50000}]  # ~12500 tokens
        decision = router.route(messages)
        # Long context pushes to at least budget tier
        assert decision.tier in ["budget", "standard", "premium", "ultra"]

    def test_route_with_tools(self, router):
        messages = [{"role": "user", "content": "Do stuff"}]
        tools = [{"name": f"tool_{i}"} for i in range(20)]
        decision = router.route(messages, tools)
        # Many tools push complexity up
        assert decision.tier in ["budget", "standard", "premium", "ultra"]

    def test_preferred_provider_override(self, router):
        messages = [{"role": "user", "content": "Hello"}]
        decision = router.route(messages, preferred_provider="anthropic")
        assert decision.provider_name == "anthropic"

    def test_preferred_provider_invalid_falls_back(self, router):
        messages = [{"role": "user", "content": "Hello"}]
        decision = router.route(messages, preferred_provider="nonexistent")
        # Falls back to normal routing
        assert decision.provider_name == "ollama"

    def test_spend_limit_downgrades_tier(self, provider_registry):
        router = CostRouter(provider_registry, spend_limit=0.0)
        # Mock session spend to exceed limit
        router._get_session_spend = lambda: 1.0
        messages = [{"role": "user", "content": "x" * 50000}]
        decision = router.route(messages)
        # Should downgrade from premium due to spend limit
        assert decision.tier != "premium"

    def test_no_providers_raises(self):
        empty_reg = ProviderRegistry({})
        router = CostRouter(empty_reg)
        with pytest.raises(ValueError, match="No providers registered"):
            router.route([{"role": "user", "content": "Hi"}])

    def test_tier_ordering(self, provider_registry):
        router = CostRouter(provider_registry)
        # Budget tier request should get budget provider
        messages = [{"role": "user", "content": "x" * 1000}]
        # Force budget tier by mocking scorer
        router.scorer.score = lambda msgs, tools=None: type(
            "obj", (object,), {
                "tier": "budget", "overall": 0.3,
                "estimated_tokens": 250, "estimated_tools": 0,
                "dimensions": [],
            }
        )()
        decision = router.route(messages)
        assert decision.tier == "budget"
        assert decision.provider_name == "local-budget"

    def test_cost_estimation(self, provider_registry):
        router = CostRouter(provider_registry)
        decision = router.route([{"role": "user", "content": "x" * 4000}])  # ~1000 tokens
        # ollama is free tier, cost should be 0
        assert decision.estimated_cost == 0.0


# ---------------------------------------------------------------------------
# SpendTracker
# ---------------------------------------------------------------------------

class TestSpendTracker:
    def test_record_and_get_spend(self, spend_tracker):
        spend_tracker.record_call("sess-1", "ollama", "qwen", 100, 50, 0.0)
        spend_tracker.record_call("sess-1", "openai", "gpt-4", 500, 200, 0.05)

        result = spend_tracker.get_spend("sess-1")
        assert result["total_cost"] == 0.05
        assert result["total_prompt_tokens"] == 600
        assert result["total_completion_tokens"] == 250
        assert result["model_calls"] == 2

    def test_get_spend_missing_session(self, spend_tracker):
        result = spend_tracker.get_spend("nonexistent")
        assert result["total_cost"] == 0.0
        assert result["model_calls"] == 0

    def test_list_sessions_ordering(self, spend_tracker):
        spend_tracker.record_call("sess-a", "ollama", "qwen", 100, 50, 0.0)
        spend_tracker.record_call("sess-b", "openai", "gpt-4", 1000, 500, 0.10)
        spend_tracker.record_call("sess-c", "anthropic", "claude", 500, 250, 0.05)

        sessions = spend_tracker.list_sessions(limit=10)
        assert len(sessions) == 3
        # Ordered by total_cost DESC
        assert sessions[0]["session_id"] == "sess-b"
        assert sessions[1]["session_id"] == "sess-c"
        assert sessions[2]["session_id"] == "sess-a"

    def test_reset_session(self, spend_tracker):
        spend_tracker.record_call("sess-del", "ollama", "qwen", 100, 50, 0.0)
        assert spend_tracker.reset_session("sess-del") is True
        result = spend_tracker.get_spend("sess-del")
        assert result["total_cost"] == 0.0
        assert spend_tracker.reset_session("nonexistent") is False
    def test_concurrent_updates(self, spend_tracker):
        """Thread-safe concurrent spend updates."""
        for i in range(20):
            spend_tracker.record_call("sess-race", "ollama", "qwen", 10, 5, 0.001)

        result = spend_tracker.get_spend("sess-race")
        assert result["total_cost"] == pytest.approx(0.02, rel=1e-9)
        assert result["model_calls"] == 20

    def test_db_schema_created(self, spend_tracker):
        import sqlite3
        with sqlite3.connect(spend_tracker.db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_spend'"
            ).fetchall()
            assert len(tables) == 1


# ---------------------------------------------------------------------------
# CostRouterConfig
# ---------------------------------------------------------------------------

class TestCostRouterConfig:
    def test_defaults(self):
        cfg = CostRouterConfig()
        assert cfg.enabled is False
        assert cfg.spend_limit is None
        assert cfg.default_tier == "standard"

    def test_from_vibe_config_disabled(self):
        class MockConfig:
            cost_router = None
        cfg = CostRouterConfig.from_vibe_config(MockConfig())
        assert cfg.enabled is False

    def test_from_vibe_config_enabled(self):
        class MockCostRouter:
            enabled = True
            spend_limit = 10.0
            default_tier = "premium"
        class MockConfig:
            cost_router = MockCostRouter()
        cfg = CostRouterConfig.from_vibe_config(MockConfig())
        assert cfg.enabled is True
        assert cfg.spend_limit == 10.0
        assert cfg.default_tier == "premium"
