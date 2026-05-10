"""Recovery rule registry — map error patterns to recovery actions."""

from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class RecoveryRuleDB:
    """Registry for error-recovery rules.

    Maps error patterns to recovery actions (retry_with, fallback_to, ask_user).
    Attempt limits are tracked in session_state (in-memory), NOT persisted to DB.
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
        error_pattern: str,
        recovery_action: str,
        recovery_args: dict[str, Any],
        tool_name: str | None = None,
        max_attempts: int = 1,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Add a recovery rule.

        Args:
            error_pattern: Regex or glob pattern to match against error messages.
            recovery_action: One of "retry_with", "fallback_to", "ask_user".
            recovery_args: Arguments for the recovery action.
            tool_name: Optional tool name to scope the rule; None means global.
            max_attempts: Max times this rule may fire per session.
            source: How this preference was created.
        """
        rule = PreferenceRule(
            pattern=error_pattern,
            action=recovery_action,
            action_args={
                "recovery_args": recovery_args,
                "tool_name": tool_name,
                "max_attempts": max_attempts,
            },
            source=source,
        )
        if self._policy:
            # Remove existing rule for same pattern + tool_name combination
            self._policy.rules = [
                r
                for r in self._policy.rules
                if not (r.pattern == error_pattern and r.action_args.get("tool_name") == tool_name)
            ]
            self._policy.add_rule(rule)
            self._save()
        return rule

    def find_recovery(
        self,
        error_message: str,
        tool_name: str | None = None,
        session_state: dict[str, Any] | None = None,
    ) -> PreferenceRule | None:
        """Find a matching recovery rule for an error message.

        Args:
            error_message: The error message to match against.
            tool_name: Optional tool name for scoping.
            session_state: In-memory dict tracking attempt counts per rule_id.

        Returns:
            Matching PreferenceRule, or None if no match or max attempts exceeded.
        """
        if self._policy is None or not self._policy.enabled:
            return None

        if session_state is None:
            session_state = {}
        attempt_counts: dict[str, int] = session_state.setdefault("recovery_attempts", {})

        for rule in self._policy.get_enabled_rules():
            rule_tool = rule.action_args.get("tool_name")
            max_attempts = rule.action_args.get("max_attempts", 1)

            # If rule is scoped to a specific tool, require match
            if rule_tool is not None and rule_tool != tool_name:
                continue

            if not self._matches(rule.pattern, error_message):
                continue

            # Check attempt limit against session_state
            current_attempts = attempt_counts.get(rule.rule_id, 0)
            if current_attempts >= max_attempts:
                continue

            # Increment attempt count in session_state
            attempt_counts[rule.rule_id] = current_attempts + 1

            # Batch hit for persistence
            self._registry.batch_hit(self.DOMAIN, rule.rule_id)

            return rule

        return None

    def list_rules(self) -> list[PreferenceRule]:
        """List all recovery rules."""
        if self._policy is None:
            return []
        return list(self._policy.rules)

    def remove_rule(self, error_pattern: str, tool_name: str | None = None) -> bool:
        """Remove a recovery rule. Returns True if removed."""
        if self._policy is None:
            return False
        original_len = len(self._policy.rules)
        self._policy.rules = [
            r
            for r in self._policy.rules
            if not (r.pattern == error_pattern and r.action_args.get("tool_name") == tool_name)
        ]
        if len(self._policy.rules) < original_len:
            self._save()
            return True
        return False

    @staticmethod
    def _matches(pattern: str, error_message: str) -> bool:
        """Check if pattern matches error message (regex or glob)."""
        try:
            if re.search(pattern, error_message):
                return True
        except re.error:
            pass
        try:
            if fnmatch.fnmatch(error_message, pattern):
                return True
        except Exception:
            pass
        return pattern in error_message
