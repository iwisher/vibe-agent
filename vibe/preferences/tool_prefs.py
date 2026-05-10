"""Tool preference registry — default argument overrides for tool calls."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class ToolPreferenceRegistry:
    """Registry for tool argument preferences.

    Maps tool_name → default args that are merged into every tool call.
    """

    DOMAIN = "tools"

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

    def set_default_args(
        self,
        tool_name: str,
        args: dict[str, Any],
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set default arguments for a tool.

        Args:
            tool_name: Exact tool name or glob pattern (e.g., "git_*")
            args: Dict of argument name → default value
            source: How this preference was created
        """
        rule = PreferenceRule(
            pattern=tool_name,
            action="merge_args",
            action_args={"args": args},
            source=source,
        )
        # Remove existing rule for same pattern
        if self._policy:
            self._policy.rules = [r for r in self._policy.rules if r.pattern != tool_name]
            self._policy.add_rule(rule)
            self._save()
        return rule

    def remove_default_args(self, tool_name: str) -> bool:
        """Remove default args for a tool. Returns True if removed."""
        if self._policy is None:
            return False
        original_len = len(self._policy.rules)
        self._policy.rules = [r for r in self._policy.rules if r.pattern != tool_name]
        if len(self._policy.rules) < original_len:
            self._save()
            return True
        return False

    def apply(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply matching preferences to tool arguments.

        Returns a new dict with defaults merged in (user args take precedence).
        Hit counts are batched in memory and flushed on session shutdown.
        """
        if self._policy is None or not self._policy.enabled:
            return arguments

        result = dict(arguments)
        for rule in self._policy.get_enabled_rules():
            if self._matches(rule.pattern, tool_name):
                # Batch hit count in registry (not persisted yet)
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                if rule.action == "merge_args":
                    defaults = rule.action_args.get("args", {})
                    # Defaults only apply if key not already present
                    for key, val in defaults.items():
                        if key not in result:
                            result[key] = val
                elif rule.action == "append_args":
                    # For list-valued args, append defaults
                    for key, val in rule.action_args.get("args", {}).items():
                        if key not in result:
                            result[key] = val

        return result

    def list_preferences(self) -> list[PreferenceRule]:
        """List all tool preferences."""
        if self._policy is None:
            return []
        return list(self._policy.rules)

    @staticmethod
    def _matches(pattern: str, tool_name: str) -> bool:
        """Check if pattern matches tool_name (exact or glob)."""
        if pattern == tool_name:
            return True
        return fnmatch.fnmatch(tool_name, pattern)
