"""LLM Wiki Storage Layer — Tripartite Memory System (Phase 1a).

Implements Andrej Karpathy's LLM Wiki pattern:
- Incrementally maintained Markdown files with YAML frontmatter
- UUID-based page identity with human-readable [[slug]] links
- AsyncFileLock with strict lock ordering (index lock before page locks)
- Quality gates: draft/verified status, TTL expiration, backlink resolution
- BM25 search via SharedMemoryDB (FTS5) when available, else linear scan
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

import yaml

from vibe.memory.models import WikiPage

if TYPE_CHECKING:
    from vibe.memory.shared_db import SharedMemoryDB

logger = logging.getLogger(__name__)

# Pattern for [[slug]] wiki links
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _make_slug(title: str) -> str:
    """Convert a title to a URL-safe slug."""
    slug = title.lower()
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"


def _today_iso() -> str:
    return date.today().isoformat()


def _parse_page_file(path: Path) -> WikiPage | None:
    """Parse a wiki .md file into a WikiPage. Returns None on error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read wiki page %s: %s", path, e)
        return None

    meta: dict[str, Any] = {}
    content = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as e:
                logger.warning("YAML parse error in %s: %s", path, e)
            content = parts[2].strip()

    return WikiPage(
        id=meta.get("id", str(uuid.uuid4())),
        slug=meta.get("slug", _make_slug(meta.get("title", path.stem))),
        title=meta.get("title", path.stem),
        content=content,
        tags=meta.get("tags", []),
        status=meta.get("status", "draft"),
        date_created=meta.get("date_created", _today_iso()),
        last_updated=meta.get("last_updated", _today_iso()),
        citations=meta.get("citations", []),
        ttl_days=meta.get("ttl_days", 30),
        path=path,
    )


def _write_page_file(path: Path, page: WikiPage) -> None:
    """Write a WikiPage to disk as a YAML-frontmatter Markdown file."""
    fm = page.to_frontmatter_dict()
    frontmatter_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
    full_text = f"---\n{frontmatter_text}---\n\n{page.content}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(full_text, encoding="utf-8")


def _extract_outgoing_links(content: str) -> set[str]:
    """Extract [[slug]] links from wiki content."""
    return set(WIKI_LINK_RE.findall(content))


def _content_hash(content: str) -> str:
    """Compute a short SHA256 hash of content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class LLMWiki:
    """LLM Wiki storage — CRUD, YAML frontmatter, quality gates, backlinks.

    Thread-safety: uses AsyncFileLock from filelock>=3.8.
    Closable protocol: call ``await wiki.close()`` when done.
    """

    def __init__(self, base_path: str | Path, db: Optional["SharedMemoryDB"] = None) -> None:
        self.base_path = Path(base_path).expanduser()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.db = db

        # Slug index file: slug -> page_id
        self._slug_index_path = self.base_path / ".slug_index.json"
        # Global index lock (acquired before any multi-page or rebuild operations)
        self._index_lock_path = self.base_path / ".index.lock"

        # In-memory indices (loaded lazily)
        self._slug_to_id: dict[str, str] = {}    # slug -> UUID
        self._id_to_slug: dict[str, str] = {}    # UUID -> slug
        # Reverse backlink index: slug -> set[page_id] (pages that link TO that slug)
        self._backlinks: dict[str, set[str]] = {}

        self._loaded = False
        self._flash_client: Any | None = None  # FlashLLMClient — set externally if available

    def set_flash_client(self, client: Any) -> None:
        """Set the FlashLLMClient for quality gate operations."""
        self._flash_client = client

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _get_async_file_lock(self, path: Path):  # type: ignore[return]
        """Get an AsyncFileLock, falling back to sync FileLock with warning."""
        try:
            from filelock import AsyncFileLock
            return AsyncFileLock(str(path), timeout=10)
        except (ImportError, AttributeError):
            logger.warning("AsyncFileLock not available (filelock<3.8); using sync FileLock")
            from filelock import FileLock
            return FileLock(str(path), timeout=10)

    async def _load_indices(self) -> None:
        """Load slug index and build backlink index from disk. Idempotent."""
        if self._loaded:
            return
        # Load slug index
        if self._slug_index_path.exists():
            try:
                data = json.loads(self._slug_index_path.read_text())
                self._slug_to_id = data.get("slug_to_id", {})
                self._id_to_slug = {v: k for k, v in self._slug_to_id.items()}
            except (json.JSONDecodeError, OSError):
                self._slug_to_id = {}
                self._id_to_slug = {}

        # Build backlink index from all pages
        self._backlinks = {}
        for md_path in self.base_path.glob("*.md"):
            page = _parse_page_file(md_path)
            if page:
                for linked_slug in _extract_outgoing_links(page.content):
                    self._backlinks.setdefault(linked_slug, set()).add(page.id)

        self._loaded = True

    async def _save_slug_index(self) -> None:
        """Persist slug index to disk. Call while holding index lock."""
        data = {"slug_to_id": self._slug_to_id}
        self._slug_index_path.write_text(json.dumps(data, indent=2))

    def _update_backlinks_for_page(self, page: WikiPage, old_links: set[str]) -> None:
        """Update the reverse backlink index after a page write."""
        new_links = _extract_outgoing_links(page.content)
        # Remove stale backlinks
        for slug in old_links - new_links:
            self._backlinks.get(slug, set()).discard(page.id)
        # Add new backlinks
        for slug in new_links - old_links:
            self._backlinks.setdefault(slug, set()).add(page.id)

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------

    async def create_page(
        self,
        title: str,
        content: str,
        tags: list[str],
        citations: list[dict] | None = None,
        status: str = "draft",
        ttl_days: int = 30,
    ) -> WikiPage:
        """Create a new wiki page. Returns the created WikiPage."""
        await self._load_indices()

        page_id = str(uuid.uuid4())
        slug = _make_slug(title)
        # Ensure slug uniqueness: append short ID suffix if collision
        original_slug = slug
        counter = 1
        while slug in self._slug_to_id:
            slug = f"{original_slug}-{page_id[:8]}"
            counter += 1
            if counter > 10:
                break

        today = _today_iso()
        page = WikiPage(
            id=page_id,
            slug=slug,
            title=title,
            content=content,
            tags=tags,
            status=status,
            date_created=today,
            last_updated=today,
            citations=citations or [],
            ttl_days=ttl_days,
            path=self.base_path / f"{slug}.md",
        )

        index_lock = self._get_async_file_lock(self._index_lock_path)
        page_lock = self._get_async_file_lock(Path(str(page.path) + ".lock"))

        async with index_lock:
            async with page_lock:
                _write_page_file(page.path, page)
                self._slug_to_id[slug] = page_id
                self._id_to_slug[page_id] = slug
                self._update_backlinks_for_page(page, set())
                await self._save_slug_index()

        # Sync to DB if available
        if self.db is not None:
            try:
                self.db.sync_wiki_page(page)
            except Exception as e:
                logger.warning("DB sync failed for page %s: %s", page_id, e)

        logger.debug("Created wiki page: %s (%s)", title, page_id)
        return page

    async def update_page(
        self,
        page_id: str,
        content: str | None = None,
        tags: list[str] | None = None,
        citations: list[dict] | None = None,
    ) -> WikiPage:
        """Update an existing page. Returns the updated WikiPage."""
        await self._load_indices()

        page = await self.get_page(page_id)
        if page is None:
            raise KeyError(f"Wiki page not found: {page_id}")

        old_links = _extract_outgoing_links(page.content)
        old_content = page.content

        # Apply updates
        if content is not None:
            page.content = content
        if tags is not None:
            page.tags = tags
        if citations is not None:
            # Merge citations (avoid duplicates by session)
            existing_sessions = {c.get("session") for c in page.citations}
            for c in citations:
                if c.get("session") not in existing_sessions:
                    page.citations.append(c)
        page.last_updated = _today_iso()

        # Auto-promote to verified if quality criteria met
        if page.status == "draft" and page.has_distinct_sessions():
            page.status = "verified"
            logger.info("Promoted wiki page to verified: %s", page_id)

        page_lock = self._get_async_file_lock(Path(str(page.path) + ".lock"))
        async with page_lock:
            _write_page_file(page.path, page)
            self._update_backlinks_for_page(page, old_links)

        # Quality gate: contradiction detection via FlashLLMClient
        if self._flash_client is not None and content is not None:
            try:
                # Fetch content of pages that link TO this page (backlinks)
                backlink_ids_list = list(self._backlinks.get(page.slug, set()))[:3]
                existing_contents: list[str] = []
                if backlink_ids_list:
                    linked_pages = await asyncio.gather(*(self.get_page(lid) for lid in backlink_ids_list))
                    for linked_page in linked_pages:
                        if linked_page is not None:
                            existing_contents.append(linked_page.content)

                if existing_contents:
                    has_contradiction = await self._flash_client.detect_contradiction(
                        content, existing_contents
                    )
                    if has_contradiction:
                        logger.warning(
                            "Contradiction detected for page %s — downgrading to draft",
                            page_id,
                        )
                        page.status = "draft"
                        # Add contradiction flag to citations metadata
                        page.citations.append({
                            "type": "contradiction_flag",
                            "detected_at": _today_iso(),
                            "linked_pages": backlink_ids_list,
                        })
            except Exception as e:
                logger.debug("Contradiction detection failed for %s (non-fatal): %s", page_id, e)

        # Sync to DB
        if self.db is not None:
            try:
                self.db.sync_wiki_page(page)
            except Exception as e:
                logger.warning("DB sync failed for page %s: %s", page_id, e)

        logger.debug("Updated wiki page: %s", page_id)
        return page

    async def get_page(self, page_id: str) -> WikiPage | None:
        """Retrieve a page by UUID."""
        await self._load_indices()

        slug = self._id_to_slug.get(page_id)
        if slug is None:
            # Try scanning files for UUID match (fallback)
            for md_path in self.base_path.glob("*.md"):
                page = _parse_page_file(md_path)
                if page and page.id == page_id:
                    return page
            return None

        path = self.base_path / f"{slug}.md"
        if not path.exists():
            return None
        return _parse_page_file(path)

    async def get_page_by_slug(self, slug: str) -> WikiPage | None:
        """Retrieve a page by human-readable slug."""
        await self._load_indices()

        page_id = self._slug_to_id.get(slug)
        if page_id is None:
            return None
        return await self.get_page(page_id)

    async def search_pages(self, query: str, limit: int = 10) -> list[WikiPage]:
        """Search pages by query. Uses FTS5 (via SharedMemoryDB) if available, else linear scan."""
        await self._load_indices()

        if self.db is not None:
            try:
                results = self.db.search_wiki(query, limit=limit)
                page_ids = [row["page_id"] for row in results]
                if page_ids:
                    fetched_pages = await asyncio.gather(*(self.get_page(pid) for pid in page_ids))
                    return [p for p in fetched_pages if p is not None]
                return []
            except Exception as e:
                logger.warning("DB search failed, falling back to linear scan: %s", e)

        # Linear scan fallback
        q = query.lower()
        scored: list[tuple[int, WikiPage]] = []
        for md_path in self.base_path.glob("*.md"):
            page = _parse_page_file(md_path)
            if page is None:
                continue
            score = 0
            if q in page.title.lower():
                score += 3
            score += sum(2 for t in page.tags if q in t.lower())
            if q in page.content.lower():
                score += 1
            if score > 0:
                scored.append((score, page))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]

    async def list_pages(
        self, tag: str | None = None, status: str | None = None
    ) -> list[WikiPage]:
        """List all pages, optionally filtered by tag and/or status."""
        await self._load_indices()

        pages = []
        for md_path in self.base_path.glob("*.md"):
            page = _parse_page_file(md_path)
            if page is None:
                continue
            if tag is not None and tag not in page.tags:
                continue
            if status is not None and page.status != status:
                continue
            pages.append(page)

        pages.sort(key=lambda p: p.last_updated, reverse=True)
        return pages

    async def delete_page(self, page_id: str) -> bool:
        """Delete a page by UUID. Returns True if deleted."""
        await self._load_indices()

        page = await self.get_page(page_id)
        if page is None:
            return False

        index_lock = self._get_async_file_lock(self._index_lock_path)
        page_lock = self._get_async_file_lock(Path(str(page.path) + ".lock"))

        async with index_lock:
            async with page_lock:
                if page.path.exists():
                    page.path.unlink()
                lock_path = Path(str(page.path) + ".lock")
                if lock_path.exists():
                    lock_path.unlink(missing_ok=True)

                # Update slug index
                self._slug_to_id.pop(page.slug, None)
                self._id_to_slug.pop(page_id, None)

                # Remove backlinks this page contributed
                for linked_slug in _extract_outgoing_links(page.content):
                    self._backlinks.get(linked_slug, set()).discard(page_id)
                # Remove entry from backlink index for this slug
                self._backlinks.pop(page.slug, None)

                await self._save_slug_index()

        # Remove from DB
        if self.db is not None:
            try:
                self.db.delete_wiki_page(page_id)
            except Exception as e:
                logger.warning("DB delete failed for page %s: %s", page_id, e)

        logger.debug("Deleted wiki page: %s", page_id)
        return True

    async def get_backlinks(self, page_id: str) -> list[WikiPage]:
        """Get all pages that link TO the given page (via [[slug]] syntax)."""
        await self._load_indices()

        page = await self.get_page(page_id)
        if page is None:
            return []

        linking_page_ids = list(self._backlinks.get(page.slug, set()))
        if not linking_page_ids:
            return []

        linked_pages = await asyncio.gather(*(self.get_page(lid) for lid in linking_page_ids))
        return [p for p in linked_pages if p is not None]

    async def expire_drafts(self, cutoff_days: int | None = None) -> int:
        """Delete draft pages older than cutoff_days. Returns count of expired pages."""
        await self._load_indices()

        pages = await self.list_pages(status="draft")
        cutoff = datetime.now() - timedelta(days=cutoff_days or 30)
        expired_count = 0

        for page in pages:
            try:
                last_updated_dt = datetime.fromisoformat(page.last_updated)
            except ValueError:
                continue
            if last_updated_dt < cutoff:
                deleted = await self.delete_page(page.id)
                if deleted:
                    expired_count += 1
                    logger.info("Expired draft wiki page: %s (%s)", page.title, page.id)

        return expired_count

    async def promote_to_verified(self, page_id: str) -> WikiPage:
        """Manually promote a draft page to verified status."""
        page = await self.get_page(page_id)
        if page is None:
            raise KeyError(f"Wiki page not found: {page_id}")
        if page.status == "verified":
            return page
        return await self.update_page(page_id)  # update_page auto-promotes if criteria met

    async def get_status_counts(self) -> dict[str, int]:
        """Return counts of wiki pages by status: {total, verified, draft}."""
        md_files = list(self.base_path.glob("*.md"))
        total = len(md_files)
        verified = 0
        draft = 0
        for md in md_files:
            page = _parse_page_file(md)
            if page:
                if page.status == "verified":
                    verified += 1
                elif page.status == "draft":
                    draft += 1
        return {"total": total, "verified": verified, "draft": draft}

    async def close(self) -> None:
        """Release resources. Part of Closable protocol."""
        # Nothing to close for file-based storage; DB cleanup handled by SharedMemoryDB
        pass
