"""Latency-aware routing for LLM requests.

Tracks per-model latency history and uses it to make routing decisions.
Replaces static fallback chains with dynamic latency-based selection.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LatencyRecord:
    """A single latency measurement."""

    timestamp: float
    latency_ms: float
    success: bool


@dataclass
class LatencyStats:
    """Aggregated latency statistics for a model."""

    model: str
    count: int = 0
    mean_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    error_rate: float = 0.0
    last_updated: float = 0.0


class LatencyTracker:
    """Track and report latency statistics per model.

    Uses a sliding window of recent measurements to provide
    up-to-date latency estimates for routing decisions.
    """

    DEFAULT_WINDOW_SIZE = 50  # Keep last N measurements

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE):
        self._window_size = window_size
        self._records: dict[str, list[LatencyRecord]] = {}

    def record(self, model: str, latency_ms: float, success: bool = True) -> None:
        """Record a latency measurement."""
        if model not in self._records:
            self._records[model] = []
        self._records[model].append(
            LatencyRecord(timestamp=time.time(), latency_ms=latency_ms, success=success)
        )
        # Trim to window size
        if len(self._records[model]) > self._window_size:
            self._records[model] = self._records[model][-self._window_size :]

    def get_stats(self, model: str) -> LatencyStats | None:
        """Get latency statistics for a model."""
        records = self._records.get(model)
        if not records:
            return None

        latencies = [r.latency_ms for r in records]
        successes = sum(1 for r in records if r.success)

        stats = LatencyStats(model=model, count=len(records))
        stats.mean_ms = statistics.mean(latencies)
        stats.p50_ms = statistics.median(latencies)
        stats.p95_ms = (
            statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else stats.p50_ms
        )
        stats.p99_ms = (
            statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else stats.p95_ms
        )
        stats.error_rate = 1.0 - (successes / len(records))
        stats.last_updated = max(r.timestamp for r in records)
        return stats

    def get_fastest(self, models: list[str], max_error_rate: float = 0.1) -> str | None:
        """Return the model with lowest p50 latency, filtering out high-error models."""
        candidates = []
        for model in models:
            stats = self.get_stats(model)
            if stats is None:
                # No data yet — assume average
                candidates.append((model, 1000.0))
            elif stats.error_rate <= max_error_rate:
                candidates.append((model, stats.p50_ms))

        if not candidates:
            return models[0] if models else None

        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def rank_models(self, models: list[str]) -> list[tuple[str, float]]:
        """Rank models by latency (fastest first). Returns list of (model, p50_ms)."""
        ranked = []
        for model in models:
            stats = self.get_stats(model)
            if stats is None:
                ranked.append((model, float("inf")))
            else:
                ranked.append((model, stats.p50_ms))
        ranked.sort(key=lambda x: x[1])
        return ranked

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Export all stats as a dict."""
        return {model: self.get_stats(model).__dict__ for model in self._records}


class LatencyAwareRouter:
    """Router that selects models based on latency history.

    Wraps the static fallback chain with dynamic latency-based ordering.
    """

    def __init__(
        self,
        tracker: LatencyTracker | None = None,
        max_latency_ms: float | None = None,
        fallback_on_timeout: bool = True,
    ):
        self.tracker = tracker or LatencyTracker()
        self.max_latency_ms = max_latency_ms
        self.fallback_on_timeout = fallback_on_timeout

    def select_model(
        self,
        primary_model: str,
        fallback_chain: list[str],
        latency_budget_ms: float | None = None,
    ) -> list[str]:
        """Return an ordered list of models to try, ranked by latency.

        The primary model is always first, followed by fallbacks sorted by latency.
        """
        all_models = [primary_model] + [m for m in fallback_chain if m != primary_model]

        # Rank by latency (fastest first)
        ranked = self.tracker.rank_models(all_models)

        # Filter by latency budget if specified
        budget = latency_budget_ms or self.max_latency_ms
        if budget is not None:
            filtered = [(m, lat) for m, lat in ranked if lat <= budget]
            if filtered:
                return [m for m, _ in filtered]

        return [m for m, _ in ranked]

    def should_fallback(
        self, model: str, elapsed_ms: float, error: Exception | None = None
    ) -> bool:
        """Determine if we should fallback based on latency or error."""
        if error is not None:
            return True

        stats = self.tracker.get_stats(model)
        if stats is None:
            return False

        # Fallback if this call was significantly slower than p95
        if elapsed_ms > stats.p95_ms * 2:
            return True

        # Fallback if exceeding max latency
        if self.max_latency_ms is not None and elapsed_ms > self.max_latency_ms:
            return True

        return False
