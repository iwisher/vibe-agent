"""Telemetry collection for Tripartite Memory System — Phase 1a.

Collects metrics from ContextCompactor and QueryLoop to enable:
- Phase 2 trigger analysis ("What % of sessions had content >100K chars?")
- Performance monitoring for wiki operations
- Session duration tracking
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from vibe.memory.shared_db import SharedMemoryDB

logger = logging.getLogger(__name__)


class TelemetryCollector:
    """Collects telemetry from ContextCompactor and QueryLoop.

    Stores events in memory.db _telemetry table.
    If db is None, logs to Python logger only (no-op mode).
    """

    def __init__(self, db: Optional["SharedMemoryDB"] = None) -> None:
        self.db = db

    def record_compaction(
        self,
        session_id: str | None,
        content_size: int,
        token_count: int,
        strategy: str,
        was_compacted: bool,
    ) -> None:
        """Record a compaction event from ContextCompactor."""
        data = {
            "content_size": content_size,
            "token_count": token_count,
            "strategy": strategy,
            "was_compacted": was_compacted,
        }
        logger.debug(
            "Telemetry compaction: session=%s size=%d tokens=%d strategy=%s",
            session_id,
            content_size,
            token_count,
            strategy,
        )
        if self.db is not None:
            try:
                self.db.record_telemetry("compaction", session_id=session_id, data=data)
            except Exception as e:
                logger.debug("Telemetry record failed: %s", e)

    def record_session(
        self,
        session_id: str | None,
        duration_seconds: float,
        total_chars: int,
        state: str,
    ) -> None:
        """Record a session completion event from QueryLoop."""
        data = {
            "duration_seconds": duration_seconds,
            "total_chars": total_chars,
            "state": state,
        }
        logger.debug(
            "Telemetry session: session=%s duration=%.1fs chars=%d state=%s",
            session_id,
            duration_seconds,
            total_chars,
            state,
        )
        if self.db is not None:
            try:
                self.db.record_telemetry("session", session_id=session_id, data=data)
            except Exception as e:
                logger.debug("Telemetry record failed: %s", e)

    def record_wiki_op(
        self,
        op: str,
        page_id: str | None,
        session_id: str | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        """Record a wiki operation (create, update, search, etc.)."""
        data = {
            "op": op,
            "page_id": page_id,
            "duration_ms": duration_ms,
        }
        if self.db is not None:
            try:
                self.db.record_telemetry("wiki_op", session_id=session_id, data=data)
            except Exception as e:
                logger.debug("Telemetry record failed: %s", e)


class TimedOperation:
    """Context manager for timing operations."""

    def __init__(self) -> None:
        self.elapsed_ms: float = 0.0
        self._start: float = 0.0

    def __enter__(self) -> "TimedOperation":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        self.elapsed_ms = (time.monotonic() - self._start) * 1000.0
