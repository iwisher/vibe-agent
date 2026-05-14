"""Compaction policy — user preferences for context window management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


@dataclass
class CompactionConfig:
    """Configuration for context compaction behavior."""

    max_tokens: int = 8000
    preserve_recent_n: int = 4  # preserve last N messages
    preserve_summary: bool = True
    compression_ratio: float = 0.5


class CompactionPolicy:
    """User preferences for context window compaction.

    Mined from explicit commands like "keep last 4 messages" or
    "summarize older context".
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

    def set_config(self, config: CompactionConfig) -> None:
        """Set compaction configuration."""
        if self._policy is None:
            return
        # Remove existing config rules
        self._policy.rules = [r for r in self._policy.rules if r.pattern != "config"]
        self._policy.add_rule(
            PreferenceRule(
                pattern="config",
                action="set",
                action_args={
                    "max_tokens": config.max_tokens,
                    "preserve_recent_n": config.preserve_recent_n,
                    "preserve_summary": config.preserve_summary,
                    "compression_ratio": config.compression_ratio,
                },
                source=PreferenceSource.EXPLICIT,
            )
        )
        self._save()

    def get_config(self) -> CompactionConfig:
        """Get current compaction configuration."""
        if self._policy is None:
            return CompactionConfig()
        for rule in self._policy.rules:
            if rule.pattern == "config":
                args = rule.action_args
                return CompactionConfig(
                    max_tokens=args.get("max_tokens", 8000),
                    preserve_recent_n=args.get("preserve_recent_n", 4),
                    preserve_summary=args.get("preserve_summary", True),
                    compression_ratio=args.get("compression_ratio", 0.5),
                )
        return CompactionConfig()

    def set_tool_priority(self, tool_name: str, priority: str) -> None:
        """Set priority for a tool's output during compaction.

        priority: "keep" | "summarize" | "drop"
        """
        if self._policy is None:
            return
        # Remove existing rule for this tool
        self._policy.rules = [
            r for r in self._policy.rules if not (r.pattern == tool_name and r.action == "priority")
        ]
        self._policy.add_rule(
            PreferenceRule(
                pattern=tool_name,
                action="priority",
                action_args={"priority": priority},
                source=PreferenceSource.EXPLICIT,
            )
        )
        self._save()

    def get_tool_priority(self, tool_name: str) -> str | None:
        """Get priority for a tool."""
        if self._policy is None:
            return None
        for rule in self._policy.rules:
            if rule.pattern == tool_name and rule.action == "priority":
                return rule.action_args.get("priority")
        return None
