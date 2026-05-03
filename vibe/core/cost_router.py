"""Cost-aware dynamic routing for LLM requests.

Routes each request to the cheapest capable model based on prompt complexity,
token estimates, and provider cost tiers. Tracks cumulative spend per session.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibe.core.provider_registry import ProviderProfile, ProviderRegistry


# ---------------------------------------------------------------------------
# Cost tiers (relative cost units per 1K tokens)
# ---------------------------------------------------------------------------

TIER_COSTS: dict[str, float] = {
    "free": 0.0,
    "budget": 0.01,
    "standard": 0.10,
    "premium": 1.00,
    "ultra": 5.00,
}

DEFAULT_TIER_ORDER = ["free", "budget", "standard", "premium", "ultra"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ComplexityScore:
    """Score for a single prompt complexity dimension."""

    dimension: str
    score: float  # 0.0 – 1.0
    weight: float = 1.0

    @property
    def weighted_score(self) -> float:
        return self.score * self.weight


@dataclass
class ComplexityResult:
    """Aggregated complexity result for a prompt."""

    overall: float  # 0.0 – 1.0
    dimensions: list[ComplexityScore] = field(default_factory=list)
    estimated_tokens: int = 0
    estimated_tools: int = 0

    @property
    def tier(self) -> str:
        """Map overall complexity to a cost tier."""
        if self.overall < 0.2:
            return "free"
        if self.overall < 0.4:
            return "budget"
        if self.overall < 0.6:
            return "standard"
        if self.overall < 0.8:
            return "premium"
        return "ultra"


@dataclass
class RoutingDecision:
    """Result of a cost-aware routing decision."""

    provider_name: str
    model_id: str
    tier: str
    estimated_cost: float
    reason: str


# ---------------------------------------------------------------------------
# ComplexityScorer
# ---------------------------------------------------------------------------

class ComplexityScorer:
    """Estimate prompt complexity from token count, tool use, and content patterns."""

    # Heuristic weights
    TOKEN_WEIGHT = 0.40
    TOOL_WEIGHT = 0.35
    PATTERN_WEIGHT = 0.25

    # Token estimation: ~4 chars per token (rough heuristic)
    CHARS_PER_TOKEN = 4

    # Complexity thresholds (in estimated tokens)
    TOKEN_TIERS = [500, 2000, 8000, 32000]  # free, budget, standard, premium boundaries

    def score(self, messages: list[dict[str, Any]], available_tools: list[dict] | None = None) -> ComplexityResult:
        """Score prompt complexity.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            available_tools: Optional list of tool schemas the LLM may call.

        Returns:
            ComplexityResult with overall score (0.0–1.0) and tier recommendation.
        """
        # 1. Token dimension
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // self.CHARS_PER_TOKEN
        token_score = self._token_score(estimated_tokens)

        # 2. Tool dimension
        estimated_tools = len(available_tools) if available_tools else 0
        tool_score = self._tool_score(estimated_tools)

        # 3. Pattern dimension (code, reasoning, multi-step)
        pattern_score = self._pattern_score(messages)

        # Weighted aggregate
        overall = (
            token_score * self.TOKEN_WEIGHT +
            tool_score * self.TOOL_WEIGHT +
            pattern_score * self.PATTERN_WEIGHT
        )

        return ComplexityResult(
            overall=min(overall, 1.0),
            dimensions=[
                ComplexityScore("tokens", token_score, self.TOKEN_WEIGHT),
                ComplexityScore("tools", tool_score, self.TOOL_WEIGHT),
                ComplexityScore("patterns", pattern_score, self.PATTERN_WEIGHT),
            ],
            estimated_tokens=estimated_tokens,
            estimated_tools=estimated_tools,
        )

    def _token_score(self, tokens: int) -> float:
        """Map token count to 0.0–1.0 complexity score."""
        for i, threshold in enumerate(self.TOKEN_TIERS):
            if tokens < threshold:
                return i / len(self.TOKEN_TIERS)
        return 1.0

    def _tool_score(self, tool_count: int) -> float:
        """Map tool count to 0.0–1.0 complexity score."""
        if tool_count == 0:
            return 0.0
        if tool_count <= 3:
            return 0.25
        if tool_count <= 8:
            return 0.50
        if tool_count <= 15:
            return 0.75
        return 1.0

    def _pattern_score(self, messages: list[dict[str, Any]]) -> float:
        """Detect complex patterns: code, reasoning chains, multi-step requests."""
        score = 0.0
        all_text = " ".join(m.get("content", "") for m in messages).lower()

        # Code detection
        code_markers = ["```", "def ", "class ", "import ", "function", "script"]
        if any(m in all_text for m in code_markers):
            score += 0.3

        # Reasoning / planning markers
        reasoning_markers = [
            "step by step", "plan", "design", "architecture",
            "compare", "evaluate", "analyze", "debug", "refactor",
        ]
        if any(m in all_text for m in reasoning_markers):
            score += 0.25

        # Multi-step / long context
        if len(messages) > 6:
            score += 0.2
        if len(messages) > 12:
            score += 0.15

        # File / path references (likely tool-heavy)
        if "/" in all_text or "\\" in all_text:
            score += 0.1

        return min(score, 1.0)


# ---------------------------------------------------------------------------
# CostRouter
# ---------------------------------------------------------------------------

class CostRouter:
    """Route LLM requests to the cheapest capable provider/model pair."""

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        scorer: ComplexityScorer | None = None,
        default_tier: str = "standard",
        spend_limit: float | None = None,
    ):
        self.registry = provider_registry
        self.scorer = scorer or ComplexityScorer()
        self.default_tier = default_tier
        self.spend_limit = spend_limit  # in cost units; None = unlimited

    def route(
        self,
        messages: list[dict[str, Any]],
        available_tools: list[dict] | None = None,
        preferred_provider: str | None = None,
    ) -> RoutingDecision:
        """Select the cheapest capable provider/model for a prompt.

        Args:
            messages: Prompt messages.
            available_tools: Tool schemas the LLM may use.
            preferred_provider: If set, force this provider if it supports the tier.

        Returns:
            RoutingDecision with chosen provider, model, tier, and estimated cost.
        """
        complexity = self.scorer.score(messages, available_tools)
        target_tier = complexity.tier

        # If spend limit exceeded, downgrade tier
        if self.spend_limit is not None:
            current_spend = self._get_session_spend()
            if current_spend >= self.spend_limit:
                target_tier = self._downgrade_tier(target_tier)

        # Find cheapest provider supporting the target tier
        candidates = self._candidates_for_tier(target_tier)

        # Preferred provider override
        if preferred_provider:
            pref = self.registry.get(preferred_provider)
            if pref and self._provider_supports_tier(pref, target_tier):
                candidates = [pref] + [c for c in candidates if c.name != preferred_provider]

        if not candidates:
            # Fallback: use default provider with default model
            default = self.registry.list_providers()
            if default:
                first = self.registry.get(default[0])
                return RoutingDecision(
                    provider_name=first.name,
                    model_id=first.default_model or "default",
                    tier=target_tier,
                    estimated_cost=0.0,
                    reason="fallback: no tier-matching provider",
                )
            raise ValueError("No providers registered in ProviderRegistry")

        chosen = candidates[0]
        model_id = chosen.default_model or "default"
        cost = self._estimate_cost(chosen, complexity.estimated_tokens)

        return RoutingDecision(
            provider_name=chosen.name,
            model_id=model_id,
            tier=target_tier,
            estimated_cost=cost,
            reason=f"complexity={complexity.overall:.2f}, tier={target_tier}, tokens≈{complexity.estimated_tokens}",
        )

    def _candidates_for_tier(self, tier: str) -> list[ProviderProfile]:
        """Return providers sorted by cost that support at least the given tier."""
        tier_idx = DEFAULT_TIER_ORDER.index(tier)
        acceptable: list[tuple[ProviderProfile, float]] = []

        for name in self.registry.list_providers():
            provider = self.registry.get(name)
            if provider is None:
                continue
            provider_tier = getattr(provider, "cost_tier", "standard")
            provider_idx = DEFAULT_TIER_ORDER.index(provider_tier)
            # Provider must be at or above the required tier
            if provider_idx >= tier_idx:
                cost = TIER_COSTS.get(provider_tier, 0.1)
                acceptable.append((provider, cost))

        # Sort by cost ascending
        acceptable.sort(key=lambda x: x[1])
        return [p for p, _ in acceptable]

    def _provider_supports_tier(self, provider: ProviderProfile, tier: str) -> bool:
        provider_tier = getattr(provider, "cost_tier", "standard")
        return DEFAULT_TIER_ORDER.index(provider_tier) >= DEFAULT_TIER_ORDER.index(tier)

    def _downgrade_tier(self, tier: str) -> str:
        """Downgrade one step when spend limit exceeded."""
        idx = DEFAULT_TIER_ORDER.index(tier)
        if idx > 0:
            return DEFAULT_TIER_ORDER[idx - 1]
        return tier

    def _estimate_cost(self, provider: ProviderProfile, estimated_tokens: int) -> float:
        """Estimate cost in cost units for a given provider and token count."""
        cost_per_1k = getattr(provider, "cost_per_1k_prompt", 0.0)
        return cost_per_1k * (estimated_tokens / 1000.0)

    def _get_session_spend(self) -> float:
        """Get current session spend from SpendTracker (if available)."""
        if self.spend_tracker is None:
            return 0.0
        try:
            result = self.spend_tracker.get_spend(self.session_id or "default")
            return result.get("total_cost", 0.0)
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# SpendTracker
# ---------------------------------------------------------------------------

class SpendTracker:
    """Track cumulative LLM spend per session in SQLite.

    Writes to the same traces.db as SessionStore and TraceStore for
    unified observability.
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            base = os.environ.get("VIBE_MEMORY_DIR")
            if base:
                db_path = str(Path(base) / "traces.db")
            else:
                db_path = str(Path.home() / ".vibe" / "memory" / "traces.db")
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_spend (
                    session_id TEXT PRIMARY KEY,
                    total_cost REAL DEFAULT 0.0,
                    total_prompt_tokens INTEGER DEFAULT 0,
                    total_completion_tokens INTEGER DEFAULT 0,
                    model_calls INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                );
                """
            )

    def record_call(
        self,
        session_id: str,
        provider_name: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
    ) -> None:
        """Record a single LLM call's cost and token usage.

        Uses INSERT OR REPLACE to maintain a running total per session.
        """
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO session_spend (session_id, total_cost, total_prompt_tokens,
                    total_completion_tokens, model_calls, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    total_cost = total_cost + excluded.total_cost,
                    total_prompt_tokens = total_prompt_tokens + excluded.total_prompt_tokens,
                    total_completion_tokens = total_completion_tokens + excluded.total_completion_tokens,
                    model_calls = model_calls + excluded.model_calls,
                    updated_at = excluded.updated_at
                """,
                (session_id, cost, prompt_tokens, completion_tokens, 1, now, now),
            )

    def get_spend(self, session_id: str) -> dict[str, Any]:
        """Get cumulative spend for a session. Returns zeros if not found."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM session_spend WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return {
                    "session_id": session_id,
                    "total_cost": 0.0,
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                    "model_calls": 0,
                }
            return dict(row)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List sessions ordered by total cost descending."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM session_spend ORDER BY total_cost DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def reset_session(self, session_id: str) -> bool:
        """Reset spend for a session. Returns True if a row was deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM session_spend WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

class CostRouterConfig:
    """Lightweight config container for CostRouter (no pydantic dependency)."""

    def __init__(
        self,
        enabled: bool = False,
        spend_limit: float | None = None,
        default_tier: str = "standard",
    ):
        self.enabled = enabled
        self.spend_limit = spend_limit
        self.default_tier = default_tier

    @classmethod
    def from_vibe_config(cls, config: Any) -> "CostRouterConfig":
        """Extract CostRouterConfig from VibeConfig."""
        cost_cfg = getattr(config, "cost_router", None)
        if cost_cfg is None:
            return cls(enabled=False)
        return cls(
            enabled=getattr(cost_cfg, "enabled", False),
            spend_limit=getattr(cost_cfg, "spend_limit", None),
            default_tier=getattr(cost_cfg, "default_tier", "standard"),
        )
