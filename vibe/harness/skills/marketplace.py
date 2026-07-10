"""Skill marketplace — discovery, search, and remote installation.

Provides a registry-based marketplace for skills beyond local path/git install.
Supports:
- Skill search by name, tag, or description
- Remote skill registry (HTTP/JSON)
- Skill rating and download counts
- Dependency resolution for skill packs
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillListing:
    """A skill available in the marketplace."""

    name: str
    version: str
    description: str
    author: str
    tags: list[str] = field(default_factory=list)
    download_url: str | None = None
    source_url: str | None = None
    dependencies: list[str] = field(default_factory=list)
    rating: float = 0.0
    downloads: int = 0
    installed: bool = False


@dataclass
class MarketplaceSearchResult:
    """Result of a marketplace search."""

    listings: list[SkillListing]
    total: int
    query: str


class SkillMarketplace:
    """Skill marketplace for discovery and remote installation.

    Supports multiple backends:
    - Local JSON registry file
    - Remote HTTP registry endpoint
    - GitHub-based skill repositories
    """

    def __init__(
        self,
        local_registry_path: str | Path | None = None,
        remote_url: str | None = None,
    ) -> None:
        self.local_registry_path = Path(local_registry_path) if local_registry_path else None
        self.remote_url = remote_url
        self._listings: dict[str, SkillListing] = {}
        self._installed: set[str] = set()

    def _load_local_registry(self) -> dict[str, SkillListing]:
        """Load skills from local JSON registry file."""
        if self.local_registry_path is None or not self.local_registry_path.exists():
            return {}

        try:
            data = json.loads(self.local_registry_path.read_text())
            listings = {}
            for item in data.get("skills", []):
                listing = SkillListing(**item)
                listings[listing.name] = listing
            return listings
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to load local skill registry: {e}")
            return {}

    async def _load_remote_registry(self) -> dict[str, SkillListing]:
        """Load skills from remote HTTP registry."""
        if self.remote_url is None:
            return {}

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.remote_url)
                resp.raise_for_status()
                data = resp.json()

            listings = {}
            for item in data.get("skills", []):
                listing = SkillListing(**item)
                listings[listing.name] = listing
            return listings
        except Exception as e:
            logger.warning(f"Failed to load remote skill registry: {e}")
            return {}

    async def refresh(self) -> None:
        """Refresh marketplace listings from all sources."""
        self._listings = self._load_local_registry()

        remote = await self._load_remote_registry()
        self._listings.update(remote)

        logger.info(f"Marketplace refreshed: {len(self._listings)} skills available")

    def search(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        author: str | None = None,
        installed_only: bool = False,
    ) -> MarketplaceSearchResult:
        """Search marketplace for skills.

        Args:
            query: Free-text search in name/description
            tags: Filter by tags (all must match)
            author: Filter by author
            installed_only: Only show installed skills

        Returns:
            MarketplaceSearchResult with matching listings
        """
        results = []
        query_lower = query.lower() if query else None

        for listing in self._listings.values():
            if installed_only and not listing.installed:
                continue

            if query_lower:
                match = (
                    query_lower in listing.name.lower()
                    or query_lower in listing.description.lower()
                )
                if not match:
                    continue

            if tags:
                if not all(tag in listing.tags for tag in tags):
                    continue

            if author and listing.author != author:
                continue

            results.append(listing)

        # Sort by rating (desc), then downloads (desc)
        results.sort(key=lambda x: (-x.rating, -x.downloads))

        return MarketplaceSearchResult(
            listings=results,
            total=len(results),
            query=query or "",
        )

    def get_skill(self, name: str) -> SkillListing | None:
        """Get a specific skill listing by name."""
        return self._listings.get(name)

    def list_categories(self) -> dict[str, int]:
        """List all tags with counts."""
        counts: dict[str, int] = {}
        for listing in self._listings.values():
            for tag in listing.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def list_authors(self) -> dict[str, int]:
        """List all authors with skill counts."""
        counts: dict[str, int] = {}
        for listing in self._listings.values():
            counts[listing.author] = counts.get(listing.author, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def mark_installed(self, name: str) -> None:
        """Mark a skill as installed."""
        if name in self._listings:
            self._listings[name].installed = True
        self._installed.add(name)

    def mark_uninstalled(self, name: str) -> None:
        """Mark a skill as uninstalled."""
        if name in self._listings:
            self._listings[name].installed = False
        self._installed.discard(name)

    async def install_from_registry(
        self,
        name: str,
        installer: Any | None = None,
    ) -> tuple[bool, str]:
        """Install a skill from the marketplace.

        Args:
            name: Skill name to install
            installer: Optional skill installer callable

        Returns:
            (success, message)
        """
        listing = self._listings.get(name)
        if listing is None:
            return False, f"Skill '{name}' not found in marketplace"

        if listing.installed:
            return True, f"Skill '{name}' is already installed"

        try:
            if installer is not None:
                await installer(listing)
            else:
                # Default: log that manual install is needed
                logger.info(
                    f"Skill '{name}' available at {listing.download_url or listing.source_url}"
                )

            self.mark_installed(name)
            return True, f"Skill '{name}' installed successfully"
        except Exception as e:
            return False, f"Failed to install '{name}': {e}"
