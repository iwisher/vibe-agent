"""TelemetryCollector — decoupled telemetry access for CLI and services.

Provides a clean API for querying telemetry data without accessing
wiki.db.conn directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TelemetrySummary:
    """Summary of telemetry data for a time period."""

    sessions_count: int = 0
    avg_duration_seconds: float = 0.0
    compactions_count: int = 0
    errors_count: int = 0


class TelemetryCollector:
    """Collects and queries telemetry data.

    Wraps the underlying database connection, providing a clean API
    that doesn't expose raw conn/cursor to callers.
    """

    def __init__(self, db_connection: Any | None = None) -> None:
        self._db = db_connection

    def set_db(self, db_connection: Any) -> None:
        """Set the database connection (for lazy initialization)."""
        self._db = db_connection

    def get_summary(self, hours: int = 24) -> TelemetrySummary:
        """Get telemetry summary for the last N hours.

        Args:
            hours: Time window in hours

        Returns:
            TelemetrySummary with aggregated metrics
        """
        if self._db is None:
            return TelemetrySummary()

        try:
            import time

            cutoff = time.time() - (hours * 3600)

            # Session stats
            cursor = self._db.conn.execute(
                "SELECT COUNT(*), AVG(duration_seconds) FROM _telemetry WHERE type = 'session' AND timestamp > ?",
                (cutoff,),
            )
            row = cursor.fetchone()
            sessions_count = row[0] or 0
            avg_duration = row[1] or 0.0

            # Compactions
            cursor = self._db.conn.execute(
                "SELECT COUNT(*) FROM _telemetry WHERE type = 'compaction' AND timestamp > ?",
                (cutoff,),
            )
            compactions_count = cursor.fetchone()[0] or 0

            # Errors
            cursor = self._db.conn.execute(
                "SELECT COUNT(*) FROM _telemetry WHERE type = 'error' AND timestamp > ?",
                (cutoff,),
            )
            errors_count = cursor.fetchone()[0] or 0

            return TelemetrySummary(
                sessions_count=sessions_count,
                avg_duration_seconds=avg_duration,
                compactions_count=compactions_count,
                errors_count=errors_count,
            )
        except Exception as e:
            logger.debug("Failed to fetch telemetry summary: %s", e)
            return TelemetrySummary()

    def record_session(
        self,
        session_id: str,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record a session telemetry event.

        Args:
            session_id: Unique session identifier
            duration_seconds: Session duration
            metadata: Optional additional data

        Returns:
            True if recorded successfully
        """
        if self._db is None:
            return False

        try:
            import json
            import time

            self._db.conn.execute(
                "INSERT INTO _telemetry (type, timestamp, session_id, duration_seconds, metadata) VALUES (?, ?, ?, ?, ?)",
                (
                    "session",
                    time.time(),
                    session_id,
                    duration_seconds,
                    json.dumps(metadata or {}),
                ),
            )
            self._db.conn.commit()
            return True
        except Exception as e:
            logger.debug("Failed to record session telemetry: %s", e)
            return False

    def record_compaction(self, pages_affected: int) -> bool:
        """Record a compaction telemetry event.

        Args:
            pages_affected: Number of pages affected by compaction

        Returns:
            True if recorded successfully
        """
        if self._db is None:
            return False

        try:
            import time

            self._db.conn.execute(
                "INSERT INTO _telemetry (type, timestamp, pages_affected) VALUES (?, ?, ?)",
                ("compaction", time.time(), pages_affected),
            )
            self._db.conn.commit()
            return True
        except Exception as e:
            logger.debug("Failed to record compaction telemetry: %s", e)
            return False
