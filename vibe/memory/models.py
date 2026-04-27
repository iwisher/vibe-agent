"""Data models for the Tripartite Memory System.

Defines WikiPage and IndexNode dataclasses used by LLMWiki and PageIndex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WikiPage:
    """A single page in the LLM Wiki."""

    id: str          # UUID string — never changes
    slug: str        # human-readable slug derived from title
    title: str
    content: str     # body of the markdown (after frontmatter)
    tags: list[str]
    status: str      # draft | verified | expired
    date_created: str  # ISO date string (YYYY-MM-DD)
    last_updated: str  # ISO date string (YYYY-MM-DD)
    citations: list[dict]
    ttl_days: int
    path: Path       # absolute path to .md file

    def to_frontmatter_dict(self) -> dict:
        """Serialize to YAML frontmatter dict."""
        return {
            "id": self.id,
            "title": self.title,
            "slug": self.slug,
            "date_created": self.date_created,
            "last_updated": self.last_updated,
            "tags": self.tags,
            "status": self.status,
            "citations": self.citations,
            "ttl_days": self.ttl_days,
        }

    def has_distinct_sessions(self) -> bool:
        """Return True if page has ≥2 citations from distinct sessions."""
        sessions = {c.get("session") for c in self.citations if c.get("session")}
        return len(sessions) >= 2


@dataclass
class IndexNode:
    """A node in the PageIndex JSON tree."""

    node_id: str
    title: str
    description: str
    file_path: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    sub_index_path: Optional[str] = None
    sub_nodes: list["IndexNode"] = field(default_factory=list)
    confidence: float = 0.0  # used during routing results

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        d: dict = {
            "node_id": self.node_id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "sub_nodes": [n.to_dict() for n in self.sub_nodes],
        }
        if self.file_path:
            d["file_path"] = self.file_path
        if self.sub_index_path:
            d["sub_index_path"] = self.sub_index_path
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "IndexNode":
        """Deserialize from JSON-compatible dict."""
        return cls(
            node_id=d.get("node_id", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            file_path=d.get("file_path"),
            tags=d.get("tags", []),
            sub_index_path=d.get("sub_index_path"),
            sub_nodes=[cls.from_dict(n) for n in d.get("sub_nodes", [])],
        )
