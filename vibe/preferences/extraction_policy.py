"""Extraction policy registry — skip patterns, auto-tags, and merge thresholds."""

from __future__ import annotations

import logging
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class ExtractionPolicy:
    """Registry for content-extraction preferences.

    Controls which queries should skip extraction, which tags are auto-applied
    to content, and the merge threshold for combining extracted chunks.
    """

    DOMAIN = "extraction"

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

    def _get_field(self, key: str, default: Any) -> Any:
        """Read a domain-level setting from a meta-rule."""
        if self._policy is None:
            return default
        for rule in self._policy.rules:
            if rule.pattern == f"__meta__:{key}":
                return rule.action_args.get("value", default)
        return default

    def _set_field(self, key: str, value: Any) -> None:
        """Write a domain-level setting into a meta-rule."""
        if self._policy is None:
            return
        meta_pattern = f"__meta__:{key}"
        # Remove existing meta-rule for this key
        self._policy.rules = [r for r in self._policy.rules if r.pattern != meta_pattern]
        rule = PreferenceRule(
            pattern=meta_pattern,
            action="set_field",
            action_args={"value": value},
            source=PreferenceSource.EXPLICIT,
        )
        self._policy.add_rule(rule)
        self._save()

    def add_skip_pattern(self, pattern: str) -> PreferenceRule:
        """Add a case-insensitive substring pattern that causes extraction to be skipped."""
        rule = PreferenceRule(
            pattern=pattern.lower(),
            action="skip_if_contains",
            source=PreferenceSource.EXPLICIT,
        )
        if self._policy:
            self._policy.add_rule(rule)
            self._save()
        return rule

    def add_auto_tag(self, keyword: str, tag: str) -> PreferenceRule:
        """Add a keyword → tag mapping applied to content during extraction."""
        rule = PreferenceRule(
            pattern=keyword.lower(),
            action="auto_tag",
            action_args={"tag": tag},
            source=PreferenceSource.EXPLICIT,
        )
        if self._policy:
            self._policy.add_rule(rule)
            self._save()
        return rule

    def should_skip(self, query: str) -> bool:
        """Return True if the query matches any skip pattern (case-insensitive)."""
        if self._policy is None or not self._policy.enabled:
            return False

        lowered = query.lower()
        for rule in self._policy.get_enabled_rules():
            if rule.action == "skip_if_contains":
                if rule.pattern in lowered:
                    self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                    return True
        return False

    def get_tags_for_content(self, content: str) -> list[str]:
        """Return all tags whose keywords are found as substrings in content."""
        if self._policy is None or not self._policy.enabled:
            return []

        tags: list[str] = []
        lowered = content.lower()
        for rule in self._policy.get_enabled_rules():
            if rule.action == "auto_tag":
                if rule.pattern in lowered:
                    tag = rule.action_args.get("tag")
                    if tag and tag not in tags:
                        tags.append(tag)
                        self._registry.batch_hit(self.DOMAIN, rule.rule_id)
        return tags

    def set_merge_threshold(self, threshold: float) -> None:
        """Set the merge threshold for combining extracted chunks."""
        self._set_field("merge_threshold", threshold)

    def get_merge_threshold(self) -> float:
        """Get the current merge threshold (default 0.8)."""
        return float(self._get_field("merge_threshold", 0.8))
