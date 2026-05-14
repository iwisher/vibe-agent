"""Shared Pydantic models for preference layer."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PreferenceSource(str, Enum):
    """How the preference was created."""

    EXPLICIT = "explicit"  # user ran `vibe pref set ...`
    INFERRED = "inferred"  # mined from session history
    IMPORTED = "imported"  # from skill or config


class PreferenceRule(BaseModel):
    """A single preference rule."""

    model_config = {"extra": "ignore"}  # Forward-compatible with new fields

    rule_id: str = Field(
        default_factory=lambda: f"rule_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    )
    pattern: str  # regex, glob, or exact match
    action: str  # what to do (tool-specific)
    action_args: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0  # 0.0-1.0, reserved for future ML scoring
    source: PreferenceSource = PreferenceSource.EXPLICIT
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_used_at: str | None = None  # For stale rule pruning
    hit_count: int = 0  # how many times applied (batched, not per-hit)
    enabled: bool = True


class PreferencePolicy(BaseModel):
    """A collection of rules for a specific preference domain."""

    model_config = {"extra": "ignore"}

    domain: str  # e.g., "tools", "approval", "style"
    rules: list[PreferenceRule] = Field(default_factory=list)
    enabled: bool = True

    def add_rule(self, rule: PreferenceRule) -> None:
        self.rules.append(rule)

    def get_enabled_rules(self) -> list[PreferenceRule]:
        return [r for r in self.rules if r.enabled]

    def remove_rule(self, rule_id: str) -> bool:
        for i, r in enumerate(self.rules):
            if r.rule_id == rule_id:
                self.rules.pop(i)
                return True
        return False
