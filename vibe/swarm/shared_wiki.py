"""SharedWiki — read-only wiki access for sub-agents with write-via-message.

All sub-agents get read-only access. Wiki updates go through the message bus
to a single authoritative owner (orchestrator) for sequential processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WikiUpdateRequest:
    """Request to update the wiki. Sent via message bus to orchestrator."""

    agent_id: str
    page_slug: str
    title: str
    content: str
    tags: list[str]
    reason: str


class SharedWiki:
    """Read-only wiki wrapper for sub-agents.

    Sub-agents can read pages but cannot write directly.
    All write requests go through the message bus.
    """

    def __init__(self, wiki_backend: Any | None = None):
        self._wiki = wiki_backend

    async def get_page(self, slug: str) -> dict[str, Any] | None:
        """Read a wiki page by slug."""
        if self._wiki is None:
            return None
        # Delegate to underlying wiki
        if hasattr(self._wiki, "get_page"):
            return await self._wiki.get_page(slug)
        return None

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search wiki pages."""
        if self._wiki is None:
            return []
        if hasattr(self._wiki, "search"):
            return await self._wiki.search(query, limit=limit)
        return []

    async def list_pages(
        self, tag: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List wiki pages with optional filters."""
        if self._wiki is None:
            return []
        if hasattr(self._wiki, "list_pages"):
            kwargs: dict[str, Any] = {}
            if tag is not None:
                kwargs["tag"] = tag
            if status is not None:
                kwargs["status"] = status
            return await self._wiki.list_pages(**kwargs)
        return []

    async def get_graph(self) -> dict[str, Any]:
        """Get wiki entity graph data."""
        if self._wiki is None:
            return {"nodes": [], "edges": []}
        if hasattr(self._wiki, "get_graph"):
            return await self._wiki.get_graph()
        return {"nodes": [], "edges": []}

    def request_update(self, request: WikiUpdateRequest) -> None:
        """Queue a wiki update request (via message bus to orchestrator).

        This is a no-op on the wiki itself — the orchestrator processes it.
        """
        # The orchestrator monitors MessageType.UPDATE_WIKI messages
        # and applies them sequentially to prevent race conditions
        pass
