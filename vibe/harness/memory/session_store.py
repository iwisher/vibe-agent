"""SQLite-backed store for in-flight session checkpoints.

Separate from TraceStore — checkpoints are for resumption,
traces are for completed session history.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionStore:
    """Store for durable session checkpoints enabling suspension and resumption.

    Checkpoints are written on every state transition and deleted when the session
    completes (COMPLETED, ERROR, STOPPED, INCOMPLETE). If the process crashes,
    the checkpoint survives and the session can be resumed.

    Secrets are redacted before persistence via the same redactor used by TraceStore.
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            base = os.environ.get("VIBE_MEMORY_DIR")
            if base:
                db_path = str(Path(base) / "traces.db")
            else:
                db_path = str(Path.home() / ".vibe" / "memory" / "traces.db")
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            with conn:
                # WAL mode for durability during crashes (Phase 3.2)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS session_checkpoints (
                        session_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        messages_json TEXT NOT NULL,
                        plan_result_json TEXT,
                        iteration INTEGER DEFAULT 0,
                        feedback_retries INTEGER DEFAULT 0,
                        model TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_checkpoints_updated ON session_checkpoints(updated_at);
                    """
                )

    def _redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact secrets from session data before persistence."""
        try:
            from vibe.harness.security.redactor import get_default_redactor
            redactor = get_default_redactor()
            return redactor.redact_dict(data)
        except ImportError:
            return data

    def save_checkpoint(
        self,
        session_id: str,
        state: str,
        messages: list[dict[str, Any]],
        plan_result: dict[str, Any] | None = None,
        iteration: int = 0,
        feedback_retries: int = 0,
        model: str | None = None,
    ) -> None:
        """Save or update a session checkpoint.

        Uses INSERT OR REPLACE so the same session_id can be checkpointed
        multiple times (idempotent update).
        """
        now = datetime.now(timezone.utc).isoformat()
        # Redact secrets from messages before persistence
        safe_messages = [self._redact(m) for m in messages]
        safe_plan = self._redact(plan_result) if plan_result else None

        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO session_checkpoints
                    (session_id, state, messages_json, plan_result_json, iteration,
                     feedback_retries, model, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                        (SELECT created_at FROM session_checkpoints WHERE session_id = ?),
                        ?
                    ), ?)
                    """,
                    (
                        session_id,
                        state,
                        json.dumps(safe_messages),
                        json.dumps(safe_plan) if safe_plan else None,
                        iteration,
                        feedback_retries,
                        model,
                        session_id,
                        now,
                        now,
                    ),
                )

    def load_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        """Load a checkpoint by session_id. Returns None if not found."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM session_checkpoints WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "session_id": row["session_id"],
                "state": row["state"],
                "messages": json.loads(row["messages_json"]),
                "plan_result": json.loads(row["plan_result_json"]) if row["plan_result_json"] else None,
                "iteration": row["iteration"],
                "feedback_retries": row["feedback_retries"],
                "model": row["model"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_incomplete(self, limit: int = 20) -> list[dict[str, Any]]:
        """List all incomplete sessions ordered by most recently updated first."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT session_id, state, iteration, model, created_at, updated_at
                FROM session_checkpoints
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_checkpoint(self, session_id: str) -> bool:
        """Delete a checkpoint. Returns True if a row was deleted."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM session_checkpoints WHERE session_id = ?",
                    (session_id,),
                )
                return cursor.rowcount > 0

    def has_checkpoint(self, session_id: str) -> bool:
        """Check if a checkpoint exists for the given session_id."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            result = conn.execute(
                "SELECT 1 FROM session_checkpoints WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return result is not None

    def count_checkpoints(self) -> int:
        """Return total number of checkpoints."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            result = conn.execute("SELECT COUNT(*) FROM session_checkpoints").fetchone()
            return result[0] if result else 0

    def count_stale(self, max_age_hours: float = 24.0) -> int:
        """Count checkpoints older than max_age_hours that are not terminal."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM session_checkpoints
                WHERE updated_at < ?
                  AND state NOT IN ('COMPLETED', 'ERROR', 'STOPPED', 'INCOMPLETE')
                """,
                (cutoff,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def count_all(self, max_age_hours: float = 168.0) -> int:
        """Count ALL checkpoints older than max_age_hours regardless of state."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM session_checkpoints WHERE updated_at < ?",
                (cutoff,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0


    def cleanup_stale(self, max_age_hours: float = 24.0) -> int:
        """Remove checkpoints older than max_age_hours that are not COMPLETED.

        Stale checkpoints accumulate when the process is killed (SIGKILL) or
        KeyboardInterrupt strikes during the finally block before deletion runs.

        Returns:
            Number of checkpoints removed.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    DELETE FROM session_checkpoints
                    WHERE updated_at < ?
                      AND state NOT IN ('COMPLETED', 'ERROR', 'STOPPED', 'INCOMPLETE')
                    """,
                    (cutoff,),
                )
                return cursor.rowcount

    def cleanup_all(self, max_age_hours: float = 168.0) -> int:
        """Remove ALL checkpoints older than max_age_hours regardless of state.

        This is a stronger cleanup for cases where even completed checkpoints
        weren't deleted (e.g., due to a bug in earlier versions).

        Returns:
            Number of checkpoints removed.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM session_checkpoints WHERE updated_at < ?",
                    (cutoff,),
                )
                return cursor.rowcount

    def get_checkpoint_stats(self) -> dict[str, Any]:
        """Return summary statistics about checkpoints."""
        with closing(sqlite3.connect(self.db_path, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) FROM session_checkpoints").fetchone()[0]
            by_state = conn.execute(
                "SELECT state, COUNT(*) as count FROM session_checkpoints GROUP BY state"
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(updated_at) FROM session_checkpoints"
            ).fetchone()[0]
            return {
                "total": total,
                "by_state": {row["state"]: row["count"] for row in by_state},
                "oldest": oldest,
            }
