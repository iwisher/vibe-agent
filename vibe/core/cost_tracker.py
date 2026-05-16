"""Cost tracking and spend limits for LLM requests.

Integrates with SpendTracker to provide real-time cost monitoring
and enforcement of spend limits per session and globally.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostRecord:
    """A single cost record for an LLM call."""

    timestamp: float
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float
    session_id: str | None = None


@dataclass
class CostBudget:
    """Budget configuration for cost tracking."""

    session_limit: float | None = None  # Max cost per session
    daily_limit: float | None = None    # Max cost per day
    global_limit: float | None = None   # Max total cost
    warning_threshold: float = 0.8      # Warn at 80% of limit

    @classmethod
    def from_config(cls, config: Any | None) -> "CostBudget":
        """Build from VibeConfig cost_router section."""
        if config is None:
            return cls()
        cr = getattr(config, "cost_router", None)
        if cr is None:
            return cls()
        return cls(
            session_limit=getattr(cr, "session_spend_limit", None),
            daily_limit=getattr(cr, "daily_spend_limit", None),
            global_limit=getattr(cr, "global_spend_limit", None),
            warning_threshold=getattr(cr, "warning_threshold", 0.8),
        )


@dataclass
class CostSnapshot:
    """Current cost snapshot."""

    session_cost: float = 0.0
    daily_cost: float = 0.0
    global_cost: float = 0.0
    session_id: str | None = None
    limit_exceeded: bool = False
    warning_triggered: bool = False


class CostTracker:
    """Track and enforce cost budgets for LLM usage.

    Wraps SpendTracker with budget awareness and provides
    pre-flight cost checks before routing decisions.
    """

    def __init__(
        self,
        spend_tracker: Any | None = None,
        budget: CostBudget | None = None,
    ):
        self.spend_tracker = spend_tracker
        self.budget = budget or CostBudget()
        self._records: list[CostRecord] = []
        self._session_costs: dict[str, float] = {}
        self._daily_costs: dict[str, float] = {}  # key: YYYY-MM-DD

    def record(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost: float,
        session_id: str | None = None,
    ) -> CostSnapshot:
        """Record a cost and return current budget status."""
        record = CostRecord(
            timestamp=time.time(),
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=estimated_cost,
            session_id=session_id,
        )
        self._records.append(record)

        # Update session cost
        if session_id:
            self._session_costs[session_id] = self._session_costs.get(session_id, 0.0) + estimated_cost

        # Update daily cost
        day_key = time.strftime("%Y-%m-%d", time.localtime())
        self._daily_costs[day_key] = self._daily_costs.get(day_key, 0.0) + estimated_cost

        # Update spend tracker if available
        if self.spend_tracker is not None and session_id:
            try:
                self.spend_tracker.record_call(
                    session_id=session_id,
                    provider_name=provider,
                    model_id=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=estimated_cost,
                )
            except Exception:
                pass

        return self.get_snapshot(session_id)

    def get_snapshot(self, session_id: str | None = None) -> CostSnapshot:
        """Get current cost snapshot."""
        session_cost = self._session_costs.get(session_id, 0.0) if session_id else 0.0
        day_key = time.strftime("%Y-%m-%d", time.localtime())
        daily_cost = self._daily_costs.get(day_key, 0.0)
        global_cost = sum(self._daily_costs.values())

        snapshot = CostSnapshot(
            session_cost=session_cost,
            daily_cost=daily_cost,
            global_cost=global_cost,
            session_id=session_id,
        )

        # Check limits
        limits = [
            (self.budget.session_limit, session_cost),
            (self.budget.daily_limit, daily_cost),
            (self.budget.global_limit, global_cost),
        ]
        for limit, current in limits:
            if limit is not None and current >= limit:
                snapshot.limit_exceeded = True
            if limit is not None and current >= limit * self.budget.warning_threshold:
                snapshot.warning_triggered = True

        return snapshot

    def check_budget(self, session_id: str | None = None, estimated_cost: float = 0.0) -> bool:
        """Check if a proposed call would exceed budget.

        Returns True if the call is allowed, False if it would exceed budget.
        """
        snapshot = self.get_snapshot(session_id)
        if snapshot.limit_exceeded:
            return False

        # Check if adding estimated_cost would exceed any limit
        if self.budget.session_limit is not None:
            if snapshot.session_cost + estimated_cost > self.budget.session_limit:
                return False
        if self.budget.daily_limit is not None:
            if snapshot.daily_cost + estimated_cost > self.budget.daily_limit:
                return False
        if self.budget.global_limit is not None:
            if snapshot.global_cost + estimated_cost > self.budget.global_limit:
                return False

        return True

    def get_stats(self) -> dict[str, Any]:
        """Get cost statistics."""
        total_calls = len(self._records)
        total_cost = sum(r.estimated_cost for r in self._records)
        total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in self._records)

        provider_breakdown: dict[str, float] = {}
        model_breakdown: dict[str, float] = {}
        for r in self._records:
            provider_breakdown[r.provider] = provider_breakdown.get(r.provider, 0.0) + r.estimated_cost
            model_breakdown[r.model] = model_breakdown.get(r.model, 0.0) + r.estimated_cost

        return {
            "total_calls": total_calls,
            "total_cost": total_cost,
            "total_tokens": total_tokens,
            "provider_breakdown": provider_breakdown,
            "model_breakdown": model_breakdown,
            "session_costs": dict(self._session_costs),
            "daily_costs": dict(self._daily_costs),
        }
