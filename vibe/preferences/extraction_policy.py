"""Extraction policy — user preferences for knowledge extraction.

Controls what gets extracted from conversations into the tripartite memory system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


@dataclass
class ExtractionConfig:
    """Configuration for knowledge extraction."""

    auto_tag: bool = True
    min_confidence: float = 0.7
    merge_threshold: float = 0.85
    skip_patterns: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.skip_patterns is None:
            self.skip_patterns = []


class ExtractionPolicy:
    """User preferences for what gets extracted into memory.

    Mined from explicit commands like "don't save this" or
    "always tag finance conversations".
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

    def set_config(self, config: ExtractionConfig) -> None:
        """Set extraction configuration."""
        if self._policy is None:
            return
        self._policy.rules = [r for r in self._policy.rules if r.pattern != "config"]
        self._policy.add_rule(
            PreferenceRule(
                pattern="config",
                action="set",
                action_args={
                    "auto_tag": config.auto_tag,
                    "min_confidence": config.min_confidence,
                    "merge_threshold": config.merge_threshold,
                    "skip_patterns": config.skip_patterns,
                },
                source=PreferenceSource.EXPLICIT,
            )
        )
        self._save()

    def get_config(self) -> ExtractionConfig:
        """Get current extraction configuration."""
        if self._policy is None:
            return ExtractionConfig()
        for rule in self._policy.rules:
            if rule.pattern == "config":
                args = rule.action_args
                return ExtractionConfig(
                    auto_tag=args.get("auto_tag", True),
                    min_confidence=args.get("min_confidence", 0.7),
                    merge_threshold=args.get("merge_threshold", 0.85),
                    skip_patterns=args.get("skip_patterns", []),
                )
        return ExtractionConfig()

    def add_skip_pattern(self, pattern: str) -> None:
        """Add a pattern to skip during extraction.

        Patterns are matched case-insensitively against message content.
        """
        if self._policy is None:
            return
        config = self.get_config()
        pattern_lower = pattern.lower()
        if pattern_lower not in [p.lower() for p in config.skip_patterns]:
            config.skip_patterns.append(pattern)
            self.set_config(config)

    def should_skip(self, content: str) -> bool:
        """Check if content should be skipped during extraction."""
        if self._policy is None or not self._policy.enabled:
            return False
        config = self.get_config()
        content_lower = content.lower()
        for pattern in config.skip_patterns:
            if pattern.lower() in content_lower:
                return True
        return False

    def add_auto_tag(self, keyword: str, tag: str) -> None:
        """Add an auto-tag rule: if keyword appears, tag with tag."""
        if self._policy is None:
            return
        self._policy.add_rule(
            PreferenceRule(
                pattern=keyword,
                action="auto_tag",
                action_args={"tag": tag},
                source=PreferenceSource.EXPLICIT,
            )
        )
        self._save()

    def get_tags(self, content: str) -> list[str]:
        """Get auto-tags for content."""
        if self._policy is None:
            return []
        tags = []
        content_lower = content.lower()
        for rule in self._policy.get_enabled_rules():
            if rule.action == "auto_tag":
                if rule.pattern.lower() in content_lower:
                    tag = rule.action_args.get("tag")
                    if tag:
                        tags.append(tag)
        return tags
