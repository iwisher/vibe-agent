"""Tests for latency-aware routing."""

import pytest

from vibe.core.latency_tracker import LatencyAwareRouter, LatencyStats, LatencyTracker


class TestLatencyTracker:
    def test_record_and_get_stats(self):
        tracker = LatencyTracker()
        tracker.record("model-a", 100.0, success=True)
        tracker.record("model-a", 200.0, success=True)
        tracker.record("model-a", 150.0, success=True)

        stats = tracker.get_stats("model-a")
        assert stats is not None
        assert stats.count == 3
        assert stats.mean_ms == pytest.approx(150.0)
        assert stats.p50_ms == 150.0
        assert stats.error_rate == 0.0

    def test_window_size_limit(self):
        tracker = LatencyTracker(window_size=5)
        for i in range(10):
            tracker.record("model-a", float(i * 100), success=True)

        stats = tracker.get_stats("model-a")
        assert stats is not None
        assert stats.count == 5
        # Should be last 5: 500, 600, 700, 800, 900
        assert stats.mean_ms == pytest.approx(700.0)

    def test_error_rate_tracking(self):
        tracker = LatencyTracker()
        tracker.record("model-a", 100.0, success=True)
        tracker.record("model-a", 200.0, success=False)
        tracker.record("model-a", 150.0, success=True)

        stats = tracker.get_stats("model-a")
        assert stats is not None
        assert stats.error_rate == pytest.approx(0.333, abs=0.01)

    def test_get_fastest(self):
        tracker = LatencyTracker()
        tracker.record("fast", 50.0, success=True)
        tracker.record("slow", 500.0, success=True)
        tracker.record("medium", 200.0, success=True)

        fastest = tracker.get_fastest(["fast", "slow", "medium"])
        assert fastest == "fast"

    def test_get_fastest_filters_high_error(self):
        tracker = LatencyTracker()
        tracker.record("fast-but-broken", 10.0, success=False)
        tracker.record("fast-but-broken", 10.0, success=False)
        tracker.record("slower-but-reliable", 100.0, success=True)

        fastest = tracker.get_fastest(["fast-but-broken", "slower-but-reliable"], max_error_rate=0.1)
        assert fastest == "slower-but-reliable"

    def test_get_fastest_no_data(self):
        tracker = LatencyTracker()
        fastest = tracker.get_fastest(["unknown"])
        assert fastest == "unknown"

    def test_rank_models(self):
        tracker = LatencyTracker()
        tracker.record("b", 200.0, success=True)
        tracker.record("a", 100.0, success=True)
        tracker.record("c", 300.0, success=True)

        ranked = tracker.rank_models(["a", "b", "c"])
        assert ranked[0][0] == "a"
        assert ranked[1][0] == "b"
        assert ranked[2][0] == "c"

    def test_to_dict(self):
        tracker = LatencyTracker()
        tracker.record("model-a", 100.0, success=True)
        d = tracker.to_dict()
        assert "model-a" in d
        assert d["model-a"]["count"] == 1


class TestLatencyAwareRouter:
    def test_select_model_ranks_by_latency(self):
        tracker = LatencyTracker()
        tracker.record("primary", 500.0, success=True)
        tracker.record("fallback1", 100.0, success=True)
        tracker.record("fallback2", 200.0, success=True)

        router = LatencyAwareRouter(tracker=tracker)
        ordered = router.select_model("primary", ["fallback1", "fallback2"])

        # fallback1 is fastest, then fallback2, then primary
        assert ordered == ["fallback1", "fallback2", "primary"]

    def test_select_model_with_latency_budget(self):
        tracker = LatencyTracker()
        tracker.record("fast", 50.0, success=True)
        tracker.record("slow", 500.0, success=True)

        router = LatencyAwareRouter(tracker=tracker, max_latency_ms=100.0)
        ordered = router.select_model("slow", ["fast"])

        # Only fast is under the 100ms budget
        assert ordered == ["fast"]

    def test_should_fallback_on_error(self):
        router = LatencyAwareRouter()
        assert router.should_fallback("model", 100.0, error=Exception("fail")) is True

    def test_should_fallback_on_timeout(self):
        tracker = LatencyTracker()
        for _ in range(10):
            tracker.record("model", 100.0, success=True)

        router = LatencyAwareRouter(tracker=tracker, max_latency_ms=150.0)
        assert router.should_fallback("model", 200.0) is True

    def test_should_not_fallback_normal(self):
        tracker = LatencyTracker()
        for _ in range(10):
            tracker.record("model", 100.0, success=True)

        router = LatencyAwareRouter(tracker=tracker)
        assert router.should_fallback("model", 150.0) is False
