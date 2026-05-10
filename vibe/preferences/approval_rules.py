"""Approval rules registry — policy-driven tool call approval decisions."""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


@dataclass
class ApprovalDecision:
    """Result of an approval rule check."""

    action: str  # "allow", "deny", "ask"
    reason: str
    rule_id: str | None = None


class ApprovalPolicyDB:
    """Registry for tool approval rules.

    Rules are evaluated in security order: deny rules first, then allow rules.
    If no rule matches, the default action is "ask".
    """

    DOMAIN = "approval"

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
        tool_pattern: str,
        action: str,
        path_pattern: str | None = None,
        arg_constraints: dict[str, Any] | None = None,
        min_confidence: float = 0.8,
    ) -> PreferenceRule:
        """Add an approval rule.

        Args:
            tool_pattern: Exact tool name or glob pattern (e.g., "file_*")
            action: "allow" or "deny"
            path_pattern: Optional glob pattern for path arguments
            arg_constraints: Optional dict of argument constraints
            min_confidence: Minimum confidence threshold for the rule
        """
        rule = PreferenceRule(
            pattern=tool_pattern,
            action=action,
            action_args={
                "path_pattern": path_pattern,
                "arg_constraints": arg_constraints or {},
                "min_confidence": min_confidence,
            },
            confidence=min_confidence,
            source=PreferenceSource.EXPLICIT,
        )
        if self._policy:
            self._policy.add_rule(rule)
            self._save()
        return rule

    def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_result_summary: dict[str, Any] | None = None,
    ) -> ApprovalDecision:
        """Check approval rules for a tool call.

        Deny rules are evaluated before allow rules for security.
        Returns "ask" if no rule matches.
        """
        if self._policy is None or not self._policy.enabled:
            return ApprovalDecision(action="ask", reason="No approval policy loaded")

        enabled_rules = self._policy.get_enabled_rules()
        deny_rules = [r for r in enabled_rules if r.action == "deny"]
        allow_rules = [r for r in enabled_rules if r.action == "allow"]

        # Evaluate deny rules first
        for rule in deny_rules:
            if self._matches(rule, tool_name, arguments):
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                return ApprovalDecision(
                    action="deny",
                    reason=f"Matched deny rule for pattern '{rule.pattern}'",
                    rule_id=rule.rule_id,
                )

        # Then evaluate allow rules
        for rule in allow_rules:
            if self._matches(rule, tool_name, arguments):
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                return ApprovalDecision(
                    action="allow",
                    reason=f"Matched allow rule for pattern '{rule.pattern}'",
                    rule_id=rule.rule_id,
                )

        return ApprovalDecision(action="ask", reason="No matching approval rule found")

    def learn_from_decision(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_decision: str,
        context: dict[str, Any] | None = None,
    ) -> PreferenceRule | None:
        """Learn from a user decision and create an inferred rule.

        Args:
            tool_name: The tool that was used
            arguments: The arguments that were used
            user_decision: "allow" or "deny"
            context: Optional context about the decision
        """
        if user_decision not in ("allow", "deny"):
            logger.warning("Invalid user_decision '%s', expected 'allow' or 'deny'", user_decision)
            return None

        # Build a tool pattern from the tool name
        tool_pattern = tool_name

        # Extract path pattern from arguments if present
        path_pattern = None
        for key in ("path", "file", "dest", "target"):
            if key in arguments:
                path_pattern = str(arguments[key])
                break

        rule = PreferenceRule(
            pattern=tool_pattern,
            action=user_decision,
            action_args={
                "path_pattern": path_pattern,
                "arg_constraints": {},
                "learned_context": context or {},
            },
            confidence=0.8,
            source=PreferenceSource.INFERRED,
        )
        if self._policy:
            self._policy.add_rule(rule)
            self._save()
        return rule

    def list_rules(self) -> list[PreferenceRule]:
        """List all approval rules."""
        if self._policy is None:
            return []
        return list(self._policy.rules)

    @staticmethod
    def _matches(rule: PreferenceRule, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if a rule matches a tool call.

        Matches tool name via exact match or fnmatch glob.
        If the rule has a path_pattern, resolves the path and matches via fnmatch.
        If arg_constraints are present, all must be satisfied.
        """
        # Tool name match
        pattern = rule.pattern
        if pattern != tool_name and not fnmatch.fnmatch(tool_name, pattern):
            return False

        action_args = rule.action_args or {}

        # Path pattern match
        path_pattern = action_args.get("path_pattern")
        if path_pattern:
            # Look for path-like arguments
            path_value = None
            for key in ("path", "file", "dest", "target"):
                if key in arguments:
                    path_value = arguments[key]
                    break

            if path_value is not None:
                try:
                    resolved = Path(str(path_value)).resolve()
                    # Match against resolved path string
                    if not fnmatch.fnmatch(str(resolved), path_pattern):
                        return False
                except (OSError, ValueError):
                    # If resolution fails, fall back to raw string match
                    if not fnmatch.fnmatch(str(path_value), path_pattern):
                        return False
            else:
                # Rule requires path pattern but no path argument present
                return False

        # Argument constraints match
        arg_constraints = action_args.get("arg_constraints") or {}
        for key, expected in arg_constraints.items():
            if key not in arguments:
                return False
            if arguments[key] != expected:
                return False

        return True
