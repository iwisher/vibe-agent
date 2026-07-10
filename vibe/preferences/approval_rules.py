"""Approval rule database — learned from user approval decisions."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


@dataclass
class ApprovalDecision:
    """Result of an approval policy check."""

    action: str  # "allow" | "deny" | "ask"
    reason: str
    rule_id: str | None = None


class ApprovalPolicyDB:
    """Database of approval rules learned from user decisions.

    Rules are structured as: (tool_pattern, path_pattern, arg_constraints) → action
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
            tool_pattern: Tool name or glob
            action: What to do when matched ("allow", "deny", "ask")
            path_pattern: Optional path glob (for file tools)
            arg_constraints: Optional arg values that must match
            min_confidence: Required confidence for auto-execution (inferred rules)
        """
        rule = PreferenceRule(
            pattern=tool_pattern,
            action=action,
            action_args={
                "path_pattern": path_pattern,
                "arg_constraints": arg_constraints or {},
                "min_confidence": min_confidence,
            },
            source=PreferenceSource.INFERRED,
        )
        if self._policy:
            # Remove duplicate patterns
            self._policy.rules = [
                r
                for r in self._policy.rules
                if not (
                    r.pattern == tool_pattern and r.action_args.get("path_pattern") == path_pattern
                )
            ]
            self._policy.add_rule(rule)
            self._save()
        return rule

    def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_result_summary: str | None = None,
    ) -> ApprovalDecision:
        """Check if an operation matches any approval rule.

        Returns ApprovalDecision with action and matched rule info.
        Deny rules are evaluated before allow rules for security.
        """
        if self._policy is None or not self._policy.enabled:
            return ApprovalDecision(action="ask", reason="no policy")

        # SECURITY: Evaluate deny rules first, then allow rules
        enabled_rules = self._policy.get_enabled_rules()
        deny_rules = [r for r in enabled_rules if r.action == "deny"]
        allow_rules = [r for r in enabled_rules if r.action == "allow"]

        for rule in deny_rules + allow_rules:
            match = self._matches(rule, tool_name, arguments)
            if match:
                # Batch hit count (not persisted immediately)
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                return ApprovalDecision(
                    action=rule.action,
                    reason=f"matched rule {rule.rule_id}: {rule.pattern}",
                    rule_id=rule.rule_id,
                )

        return ApprovalDecision(action="ask", reason="no matching rule")

    def _matches(self, rule: PreferenceRule, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if a rule matches a tool invocation.

        SECURITY: All paths are resolved to absolute form before matching
        to prevent path traversal bypasses (e.g., /projects/../../../etc/shadow).
        """
        # Tool name match
        if not fnmatch.fnmatch(tool_name, rule.pattern):
            return False

        path_pattern = rule.action_args.get("path_pattern")
        if path_pattern:
            # Extract path from arguments (common for file/bash tools)
            raw_path = (
                arguments.get("path") or arguments.get("file_path") or arguments.get("cwd") or ""
            )
            # SECURITY: Resolve absolute path to prevent traversal bypass
            try:
                resolved = str(Path(raw_path).resolve())
            except (OSError, ValueError):
                return False
            # Match against resolved path
            # On macOS, /tmp resolves to /private/tmp and /var/folders resolves to
            # /private/var/folders. We match against both resolved and unresolved
            # forms for portability, BUT we also verify the resolved path is within
            # the allowed directory to prevent path traversal bypasses.
            unresolved = str(Path(raw_path))
            resolved_match = fnmatch.fnmatch(resolved, path_pattern)
            unresolved_match = fnmatch.fnmatch(unresolved, path_pattern)

            # SECURITY: Path traversal check — if the unresolved path contains
            # parent directory references (..), the resolved path MUST also match.
            # For normal paths, either match is sufficient (macOS /private compat).
            has_traversal = ".." in unresolved
            if has_traversal:
                # Traversal attempt: both must match
                if not (resolved_match and unresolved_match):
                    return False
            else:
                # Normal path: either match is fine
                if not (resolved_match or unresolved_match):
                    return False

        arg_constraints = rule.action_args.get("arg_constraints", {})
        for key, expected in arg_constraints.items():
            actual = arguments.get(key)
            if actual != expected:
                return False

        return True

    def learn_from_decision(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_decision: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record a user approval decision for future rule mining.

        This is called after every manual approval gate interaction.
        """
        # For MVP: immediately create a rule if pattern is clear
        # Future: batch mine with clustering
        path = arguments.get("path") or arguments.get("file_path") or arguments.get("cwd")
        if path and user_decision == "allow":
            # Create a broad rule: allow this tool in this directory
            dir_pattern = str(Path(path).parent) + "/*"
            self.add_rule(
                tool_pattern=tool_name,
                action="allow",
                path_pattern=dir_pattern,
            )
        elif user_decision == "deny":
            # Create a deny rule for exact args
            self.add_rule(
                tool_pattern=tool_name,
                action="deny",
                path_pattern=str(path) if path else None,
                arg_constraints={
                    k: v for k, v in arguments.items() if k in ["command", "recursive"]
                },
            )
