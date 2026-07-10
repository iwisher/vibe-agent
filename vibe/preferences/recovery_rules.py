"""Recovery rule database — learned from error recovery patterns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


@dataclass
class RecoveryAction:
    """A recovery action for a specific error pattern."""

    tool_name: str
    error_pattern: str  # regex or substring
    recovery_tool: str  # tool to use for recovery
    recovery_args: dict[str, Any]
    max_attempts: int = 3


class RecoveryRuleDB:
    """Database of recovery rules learned from user corrections.

    When a tool fails, check if we have a learned recovery pattern.
    Track attempt counts in session state (not persisted) to prevent loops.
    """

    DOMAIN = "recovery"

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

    def add_rule(
        self,
        tool_name: str,
        error_pattern: str,
        recovery_tool: str,
        recovery_args: dict[str, Any],
        max_attempts: int = 3,
    ) -> PreferenceRule:
        """Add a recovery rule.

        Args:
            tool_name: Tool that failed
            error_pattern: Error message substring to match
            recovery_tool: Tool to use for recovery
            recovery_args: Args for recovery tool
            max_attempts: Max recovery attempts per session
        """
        rule = PreferenceRule(
            pattern=tool_name,
            action="recover",
            action_args={
                "error_pattern": error_pattern,
                "recovery_tool": recovery_tool,
                "recovery_args": recovery_args,
                "max_attempts": max_attempts,
            },
            source=PreferenceSource.INFERRED,
        )
        if self._policy:
            # Remove duplicates
            self._policy.rules = [
                r
                for r in self._policy.rules
                if not (
                    r.pattern == tool_name and r.action_args.get("error_pattern") == error_pattern
                )
            ]
            self._policy.add_rule(rule)
            self._save()
        return rule

    def find_recovery(
        self,
        tool_name: str,
        error_message: str,
        session_state: dict[str, Any],
    ) -> RecoveryAction | None:
        """Find a recovery action for a failed tool.

        Args:
            tool_name: Tool that failed
            error_message: Error message from the tool
            session_state: Mutable session state for attempt tracking

        Returns:
            RecoveryAction if found and attempts remain, None otherwise
        """
        if self._policy is None or not self._policy.enabled:
            return None

        for rule in self._policy.get_enabled_rules():
            if rule.pattern != tool_name:
                continue

            pattern = rule.action_args.get("error_pattern", "")
            if pattern.lower() not in error_message.lower():
                continue

            # Check attempt limit using session state
            rule_key = f"recovery_attempts:{rule.rule_id}"
            attempts = session_state.get(rule_key, 0)
            max_attempts = rule.action_args.get("max_attempts", 3)

            if attempts >= max_attempts:
                continue

            # Increment attempt count in session state
            session_state[rule_key] = attempts + 1

            # Batch hit count
            self._registry.batch_hit(self.DOMAIN, rule.rule_id)

            return RecoveryAction(
                tool_name=tool_name,
                error_pattern=pattern,
                recovery_tool=rule.action_args["recovery_tool"],
                recovery_args=rule.action_args.get("recovery_args", {}),
                max_attempts=max_attempts,
            )

        return None

    def remove_rule(self, tool_name: str, error_pattern: str) -> bool:
        """Remove a recovery rule."""
        if self._policy is None:
            return False
        original_count = len(self._policy.rules)
        self._policy.rules = [
            r
            for r in self._policy.rules
            if not (r.pattern == tool_name and r.action_args.get("error_pattern") == error_pattern)
        ]
        if len(self._policy.rules) < original_count:
            self._save()
            return True
        return False
