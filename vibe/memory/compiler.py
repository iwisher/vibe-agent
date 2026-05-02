"""Wiki Compiler — nightly trace compilation with pending/ human review mechanism.

Phase 7: Scans TraceStore for recent sessions, extracts knowledge using
KnowledgeExtractor, compiles drafts into a pending/ directory, and provides
review/approve/reject workflow for human verification before promotion to
main wiki.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from vibe.memory.extraction import KnowledgeExtractor
from vibe.memory.wiki import LLMWiki, _make_slug

logger = logging.getLogger(__name__)


@dataclass
class CompilationSummary:
    """Result of a compilation run."""

    sessions_scanned: int = 0
    items_extracted: int = 0
    items_approved: int = 0
    pages_created: int = 0
    errors: int = 0


class WikiCompiler:
    """Compile trace sessions into wiki page drafts for human review.

    Workflow:
    1. Scan TraceStore for sessions within time window (default 24h)
    2. Retrieve messages for each session
    3. Extract knowledge items via KnowledgeExtractor
    4. Apply quality gates (novelty + confidence)
    5. Create wiki page drafts in pending/ directory
    6. Human reviews pending pages and approves/rejects
    7. Approved pages are moved to main wiki with status=verified
    """

    def __init__(
        self,
        trace_store: Any,
        wiki: LLMWiki,
        llm_client: Any,
        pending_wiki_path: str | Path | None = None,
        pageindex: Any | None = None,
        config: Any | None = None,
    ) -> None:
        self.trace_store = trace_store
        self.wiki = wiki
        self.llm_client = llm_client
        self.pageindex = pageindex
        self.config = config

        # Pending wiki directory — defaults to ~/.vibe/wiki/pending
        if pending_wiki_path is None:
            pending_wiki_path = Path(wiki.base_path) / "pending"
        self.pending_wiki = LLMWiki(base_path=pending_wiki_path)

        self.extractor = KnowledgeExtractor(
            llm_client=llm_client,
            wiki=wiki,
            pageindex=pageindex,
            config=config,
        )

    # ------------------------------------------------------------------
    # Compilation
    # ------------------------------------------------------------------

    async def compile_recent(
        self,
        hours: int = 24,
        novelty_threshold: float = 0.5,
        confidence_threshold: float = 0.8,
    ) -> CompilationSummary:
        """Scan recent trace sessions and compile knowledge into pending wiki.

        Args:
            hours: Time window to look back.
            novelty_threshold: Minimum novelty score for items (0.0-1.0).
            confidence_threshold: Minimum confidence score for items (0.0-1.0).

        Returns:
            CompilationSummary with counts.
        """
        summary = CompilationSummary()
        sessions = self._get_recent_sessions(hours)
        summary.sessions_scanned = len(sessions)

        for session in sessions:
            try:
                session_id = session["id"]
                messages = self._get_session_messages(session_id)
                if not messages:
                    continue

                items = await self.extractor.extract_from_session(messages, session_id)
                summary.items_extracted += len(items)

                approved = await self.extractor.apply_gates(
                    items,
                    novelty_threshold=novelty_threshold,
                    confidence_threshold=confidence_threshold,
                )
                summary.items_approved += len(approved)

                for item in approved:
                    await self._create_pending_page(item, session_id)
                    summary.pages_created += 1

            except Exception as e:
                logger.warning("Compilation failed for session %s: %s", session.get("id"), e)
                summary.errors += 1

        return summary

    def _get_recent_sessions(self, hours: int) -> list[dict[str, Any]]:
        """Retrieve sessions from the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        # If trace_store is SQLite-backed, query directly
        db_path = getattr(self.trace_store, "db_path", None)
        if db_path:
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT * FROM sessions WHERE start_time > ? ORDER BY start_time DESC",
                        (cutoff,),
                    ).fetchall()
                    return [dict(row) for row in rows]
            except Exception as e:
                logger.warning("SQLite query failed, falling back to get_recent_sessions: %s", e)

        # Fallback: use get_recent_sessions and filter in memory
        recent = self.trace_store.get_recent_sessions(limit=1000)
        return [s for s in recent if s.get("start_time", "") > cutoff]

    def _get_session_messages(self, session_id: str) -> list[Any]:
        """Retrieve messages for a given session.

        Returns message objects with ``.role`` and ``.content`` attributes so
        they are compatible with :class:`KnowledgeExtractor`.
        """
        db_path = getattr(self.trace_store, "db_path", None)
        if db_path:
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT role, content, tool_calls FROM messages WHERE session_id = ? ORDER BY id",
                        (session_id,),
                    ).fetchall()
                    messages = []
                    for row in rows:
                        msg = SimpleNamespace(
                            role=row["role"],
                            content=row["content"] or "",
                        )
                        if row["tool_calls"]:
                            try:
                                msg.tool_calls = json.loads(row["tool_calls"])
                            except json.JSONDecodeError:
                                pass
                        messages.append(msg)
                    return messages
            except Exception as e:
                logger.warning("SQLite message query failed for %s: %s", session_id, e)

        # Fallback: empty list (other backends may store differently)
        return []

    async def _create_pending_page(
        self,
        item: dict[str, Any],
        session_id: str,
    ) -> None:
        """Create a draft wiki page in the pending directory."""
        title = item.get("title", "Untitled")
        content = item.get("content", "")
        tags = item.get("tags", [])
        citations = item.get("citations", [])

        # Add compiler metadata citation
        citations.append({
            "type": "compiler_extraction",
            "session": session_id,
            "compiled_at": datetime.now(timezone.utc).isoformat(),
        })

        # Deduplicate: check if a pending page with same slug already exists
        slug = _make_slug(title)
        existing = await self.pending_wiki.get_page_by_slug(slug)
        if existing is not None:
            # Merge content instead of creating duplicate
            merged_content = f"{existing.content}\n\n---\n\n{content}"
            await self.pending_wiki.update_page(
                existing.id,
                content=merged_content,
                citations=citations,
            )
            logger.debug("Merged pending page for slug: %s", slug)
            return

        await self.pending_wiki.create_page(
            title=title,
            content=content,
            tags=tags,
            citations=citations,
            status="draft",
        )
        logger.debug("Created pending page: %s", title)

    # ------------------------------------------------------------------
    # Review workflow
    # ------------------------------------------------------------------

    async def list_pending(self) -> list[Any]:
        """List all pending wiki pages awaiting review."""
        return await self.pending_wiki.list_pages()

    async def approve_page(self, page_id: str) -> Any:
        """Approve a pending page: copy to main wiki as verified.

        Returns the promoted WikiPage.
        """
        pending_page = await self.pending_wiki.get_page(page_id)
        if pending_page is None:
            raise KeyError(f"Pending page not found: {page_id}")

        # Create in main wiki as verified
        main_page = await self.wiki.create_page(
            title=pending_page.title,
            content=pending_page.content,
            tags=pending_page.tags,
            citations=pending_page.citations,
            status="verified",
        )

        # Remove from pending
        await self.pending_wiki.delete_page(page_id)
        logger.info("Approved pending page '%s' -> main wiki (%s)", pending_page.title, main_page.id)
        return main_page

    async def reject_page(self, page_id: str) -> None:
        """Reject a pending page: delete from pending wiki."""
        pending_page = await self.pending_wiki.get_page(page_id)
        if pending_page is None:
            raise KeyError(f"Pending page not found: {page_id}")

        await self.pending_wiki.delete_page(page_id)
        logger.info("Rejected pending page: %s", pending_page.title)

    async def review_all(self, auto_approve: bool = False) -> dict[str, int]:
        """Review all pending pages.

        Args:
            auto_approve: If True, approve all pending pages without human review.

        Returns:
            Dict with 'approved' and 'rejected' counts.
        """
        pending = await self.list_pending()
        approved = 0
        rejected = 0

        for page in pending:
            try:
                if auto_approve:
                    await self.approve_page(page.id)
                    approved += 1
                else:
                    # Default: keep pending for human review
                    pass
            except Exception as e:
                logger.warning("Review failed for page %s: %s", page.id, e)

        return {"approved": approved, "rejected": rejected}
