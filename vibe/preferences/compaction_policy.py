"""Compaction policy registry — control context compaction behavior."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class CompactionStrategy(str, Enum):
    """Available compaction strategies."""

    TRUNCATE = "truncate"
    LLM_SUMMARIZE = "llm_summarize"
    OFFLOAD = "offload"
    DROP = "drop"


class CompactionPolicy:
    """Policy registry for compaction preferences.

    Stores per-domain compaction settings using the same _set_field/_get_field
    pattern as other preference policies.
    """

    DOMAIN = "compaction"

    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()

    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)

    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)

    def _set_field(
        self,
        key: str,
        value: Any,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> None:
        """Set a policy field value, replacing any existing rule for that key."""
        if self._policy is None:
            return
        self._policy.rules = [r for r in self._policy.rules if r.pattern != key]
        rule = PreferenceRule(
            pattern=key,
            action="set_field",
            action_args={"value": value},
            source=source,
        )
        self._policy.add_rule(rule)
        self._save()

    def _get_field(self, key: str, default: Any = None) -> Any:
        """Get the current value for a policy field, or default if unset."""
        if self._policy is None:
            return default
        for rule in reversed(self._policy.rules):
            if rule.pattern == key:
                return rule.action_args.get("value", default)
        return default

    # -- Public API --

    def set_strategy(
        self,
        strategy: CompactionStrategy,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set the default compaction strategy."""
        self._set_field("strategy", strategy.value, source=source)
        # Return the rule that was just added
        return (
            self._policy.rules[-1]
            if self._policy
            else PreferenceRule(
                pattern="strategy", action="set_field", action_args={"value": strategy.value}
            )
        )

    def get_strategy(self) -> CompactionStrategy | None:
        """Get the current compaction strategy, or None if unset."""
        val = self._get_field("strategy")
        if val is None:
            return None
        try:
            return CompactionStrategy(val)
        except ValueError:
            return None

    def set_drop_priority(
        self,
        priorities: list[str],
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set message-type drop priority (ordered list, lowest priority dropped first)."""
        self._set_field("drop_priority", priorities, source=source)
        return (
            self._policy.rules[-1]
            if self._policy
            else PreferenceRule(
                pattern="drop_priority", action="set_field", action_args={"value": priorities}
            )
        )

    def get_drop_priority(self) -> list[str] | None:
        """Get the current drop priority list, or None if unset."""
        return self._get_field("drop_priority")

    def set_never_summarize(
        self,
        message_types: list[str],
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set message types that should never be summarized."""
        self._set_field("never_summarize", message_types, source=source)
        return (
            self._policy.rules[-1]
            if self._policy
            else PreferenceRule(
                pattern="never_summarize", action="set_field", action_args={"value": message_types}
            )
        )

    def get_never_summarize(self) -> list[str] | None:
        """Get the list of message types to never summarize, or None if unset."""
        return self._get_field("never_summarize")

    def set_offload_threshold(
        self,
        threshold: int,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set token threshold above which offloading is triggered."""
        self._set_field("offload_threshold", threshold, source=source)
        return (
            self._policy.rules[-1]
            if self._policy
            else PreferenceRule(
                pattern="offload_threshold", action="set_field", action_args={"value": threshold}
            )
        )

    def get_offload_threshold(self) -> int | None:
        """Get the offload token threshold, or None if unset."""
        return self._get_field("offload_threshold")

    def list_settings(self) -> list[PreferenceRule]:
        """List all compaction settings."""
        if self._policy is None:
            return []
        return list(self._policy.rules)
