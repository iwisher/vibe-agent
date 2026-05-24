"""Per-tag and per-domain novelty thresholds for wiki knowledge extraction.

Replaces the single global novelty threshold with configurable thresholds
per tag/domain, enabling nuanced deduplication (e.g., stricter for finance,
looser for general knowledge).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TagThreshold:
    """Novelty threshold configuration for a specific tag/domain."""

    tag: str
    threshold: float = 0.5
    description: str = ""


class NoveltyThresholdRegistry:
    """Registry for per-tag novelty thresholds.

    Falls back to global default when no specific threshold is configured.
    """

    def __init__(self, default_threshold: float = 0.5) -> None:
        self.default_threshold = default_threshold
        self._thresholds: dict[str, TagThreshold] = {}

    def set_threshold(self, tag: str, threshold: float, description: str = "") -> None:
        """Set threshold for a specific tag."""
        self._thresholds[tag.lower()] = TagThreshold(
            tag=tag, threshold=threshold, description=description
        )

    def get_threshold(self, tags: list[str] | None = None) -> float:
        """Get the effective threshold for a set of tags.

        If multiple tags have thresholds, returns the most strict (lowest).
        If no tags match, returns the global default.
        """
        if not tags:
            return self.default_threshold

        thresholds = []
        for tag in tags:
            cfg = self._thresholds.get(tag.lower())
            if cfg:
                thresholds.append(cfg.threshold)

        if thresholds:
            # Return the most strict (lowest) threshold
            return min(thresholds)

        return self.default_threshold

    def remove_threshold(self, tag: str) -> bool:
        """Remove a tag-specific threshold."""
        key = tag.lower()
        if key in self._thresholds:
            del self._thresholds[key]
            return True
        return False

    def list_thresholds(self) -> list[TagThreshold]:
        """List all configured thresholds."""
        return sorted(self._thresholds.values(), key=lambda x: x.tag)

    def is_novel(
        self,
        novelty_score: float,
        tags: list[str] | None = None,
    ) -> bool:
        """Check if a novelty score indicates novel content.

        Args:
            novelty_score: Score from novelty detector (higher = more novel)
            tags: Tags associated with the content

        Returns:
            True if content is novel enough to warrant a new page
        """
        threshold = self.get_threshold(tags)
        return novelty_score >= threshold

    @classmethod
    def from_config(cls, config: Any) -> "NoveltyThresholdRegistry":
        """Build registry from config object."""
        default = getattr(config, "novelty_threshold", 0.5)
        registry = cls(default_threshold=default)

        tag_thresholds = getattr(config, "tag_thresholds", None)
        if tag_thresholds:
            for tag, cfg in tag_thresholds.items():
                if isinstance(cfg, dict):
                    registry.set_threshold(
                        tag=tag,
                        threshold=cfg.get("threshold", default),
                        description=cfg.get("description", ""),
                    )
                else:
                    registry.set_threshold(tag=tag, threshold=float(cfg))

        return registry
