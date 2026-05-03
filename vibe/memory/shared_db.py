"""Shared Memory Database — Tripartite Memory System (Phase 1a).

Consolidates SQLite databases into a single memory.db with:
- Schema versioning via _schema_version table
- FTS5 wiki_chunks table with porter tokenizer
- MigrationManager with explicit runner (not silent auto-migration)
- Content hash check to skip re-indexing on unchanged pages
- sessions and evals tables migrated from legacy trace/eval stores
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from vibe.memory.models import WikiPage

logger = logging.getLogger(__name__)

# Current schema version
SCHEMA_VERSION = 1

# DDL for all tables
_DDL = """
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    start_time TEXT,
    model TEXT,
    success INTEGER,
    error TEXT,
    messages TEXT,
    tool_results TEXT
);

CREATE TABLE IF NOT EXISTS evals (
    id TEXT PRIMARY KEY,
    eval_id TEXT,
    passed INTEGER,
    score REAL,
    diff TEXT,
    run_at TEXT
);

CREATE TABLE IF NOT EXISTS chunk_meta (
    chunk_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    content_hash TEXT,
    start_offset INTEGER,
    end_offset INTEGER,
    FOREIGN KEY (page_id) REFERENCES wiki_chunks(page_id)
);

CREATE TABLE IF NOT EXISTS _telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    session_id TEXT,
    data TEXT
);
"""

_WIKI_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_chunks USING fts5(
    chunk_id UNINDEXED,
    page_id UNINDEXED,
    content,
    tokenize='porter'
);
"""

# Chunk size for splitting large pages
CHUNK_SIZE = 2000  # chars


def _check_fts5_available(conn: sqlite3.Connection) -> bool:
    """Check if FTS5 is available in this SQLite build."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_check")
        return True
    except sqlite3.OperationalError:
        return False


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class MigrationManager:
    """Explicit migration runner for schema upgrades.

    Never silently auto-migrates — logs what it's doing and can be audited.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def current_version(self) -> int:
        """Get current schema version from _schema_version table."""
        try:
            row = self.conn.execute(
                "SELECT MAX(version) FROM _schema_version"
            ).fetchone()
            return row[0] or 0
        except sqlite3.OperationalError:
            return 0

    def run(self, target_version: int = SCHEMA_VERSION) -> None:
        """Run all pending migrations up to target_version."""
        current = self.current_version()
        if current >= target_version:
            return

        logger.info(
            "Running memory.db migrations: version %d → %d", current, target_version
        )
        migrations = {
            1: self._migrate_v1,
        }
        for v in range(current + 1, target_version + 1):
            if v in migrations:
                logger.info("Applying migration v%d", v)
                migrations[v]()
                self.conn.execute(
                    "INSERT INTO _schema_version (version, description) VALUES (?, ?)",
                    (v, f"Migration to schema version {v}"),
                )
                self.conn.commit()

    def migrate_from_traces_db(self, traces_db_path: Path) -> int:
        """Migrate sessions from legacy traces.db. Returns count of migrated rows."""
        if not traces_db_path.exists():
            return 0

        try:
            src = sqlite3.connect(str(traces_db_path))
            src.row_factory = sqlite3.Row
            rows = src.execute("SELECT * FROM sessions").fetchall()
            count = 0
            for row in rows:
                d = dict(row)
                self.conn.execute(
                    """INSERT OR IGNORE INTO sessions
                    (id, start_time, model, success, error, messages, tool_results)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        d.get("id"),
                        d.get("start_time"),
                        d.get("model"),
                        d.get("success"),
                        d.get("error"),
                        d.get("messages"),
                        d.get("tool_results"),
                    ),
                )
                count += 1
            self.conn.commit()
            src.close()
            logger.info("Migrated %d sessions from %s", count, traces_db_path)
            return count
        except Exception as e:
            logger.warning("Failed to migrate from traces.db: %s", e)
            return 0

    def migrate_from_evals_db(self, evals_db_path: Path) -> int:
        """Migrate evals from legacy evals.db. Returns count of migrated rows."""
        if not evals_db_path.exists():
            return 0

        try:
            src = sqlite3.connect(str(evals_db_path))
            src.row_factory = sqlite3.Row
            # Try common eval table names
            for table_name in ("eval_results", "results", "evals"):
                try:
                    rows = src.execute(f"SELECT * FROM {table_name}").fetchall()
                    count = 0
                    for row in rows:
                        d = dict(row)
                        self.conn.execute(
                            """INSERT OR IGNORE INTO evals
                            (id, eval_id, passed, score, diff, run_at)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                d.get("id"),
                                d.get("eval_id"),
                                d.get("passed"),
                                d.get("score"),
                                d.get("diff"),
                                d.get("run_at"),
                            ),
                        )
                        count += 1
                    self.conn.commit()
                    src.close()
                    logger.info("Migrated %d evals from %s", count, evals_db_path)
                    return count
                except sqlite3.OperationalError:
                    continue
            src.close()
        except Exception as e:
            logger.warning("Failed to migrate from evals.db: %s", e)
        return 0

    def _migrate_v1(self) -> None:
        """Initialize v1 schema tables."""
        # Already done by _DDL in __init__; this is a no-op placeholder
        pass


class SharedMemoryDB:
    """Consolidated SQLite database for Tripartite Memory System.

    Provides:
    - sessions table (migrated from trace_store)
    - evals table (migrated from eval_store)
    - wiki_chunks FTS5 virtual table
    - _telemetry table for Phase 2 trigger metrics
    - _schema_version table for migration tracking
    """

    def __init__(self, db_path: str | Path = "~/.vibe/memory/memory.db") -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection | None = None
        self._fts5_available = False
        self._init_db()

    def _init_db(self) -> None:
        conn = self._get_conn()
        # Apply base DDL
        conn.executescript(_DDL)

        # Check FTS5 availability and create wiki_chunks
        self._fts5_available = _check_fts5_available(conn)
        if self._fts5_available:
            conn.executescript(_WIKI_FTS_DDL)
        else:
            # Fallback: regular table instead of FTS5
            logger.warning(
                "FTS5 not available in SQLite — using plain text search for wiki_chunks"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS wiki_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    content TEXT
                )"""
            )
        conn.commit()

        # Run migrations
        migrator = MigrationManager(conn)
        migrator.run(SCHEMA_VERSION)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Context manager for explicit transactions."""
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Wiki chunk sync
    # ------------------------------------------------------------------

    def sync_wiki_page(self, page: "WikiPage") -> None:
        """Sync a wiki page to the FTS5 index. Skips if content unchanged."""
        conn = self._get_conn()
        page_id = page.id
        new_hash = _content_hash(page.content)

        # Check existing hash to skip re-indexing
        row = conn.execute(
            "SELECT content_hash FROM chunk_meta WHERE page_id = ? LIMIT 1",
            (page_id,),
        ).fetchone()
        if row and row["content_hash"] == new_hash:
            return  # Content unchanged — skip

        # Delete old chunks (atomic with insertion)
        with self._tx() as c:
            if self._fts5_available:
                c.execute("DELETE FROM wiki_chunks WHERE page_id = ?", (page_id,))
            else:
                c.execute("DELETE FROM wiki_chunks WHERE page_id = ?", (page_id,))
            c.execute("DELETE FROM chunk_meta WHERE page_id = ?", (page_id,))

            # Split content into chunks
            content = page.content
            chunks = [
                content[i: i + CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE)
            ]
            if not chunks:
                chunks = [content]

            for idx, chunk in enumerate(chunks):
                chunk_id = f"{page_id}_{idx}"
                start = idx * CHUNK_SIZE
                end = min(start + CHUNK_SIZE, len(content))

                c.execute(
                    "INSERT INTO wiki_chunks (chunk_id, page_id, content) VALUES (?, ?, ?)",
                    (chunk_id, page_id, chunk),
                )
                c.execute(
                    """INSERT OR REPLACE INTO chunk_meta
                    (chunk_id, page_id, content_hash, start_offset, end_offset)
                    VALUES (?, ?, ?, ?, ?)""",
                    (chunk_id, page_id, new_hash, start, end),
                )

    def delete_wiki_page(self, page_id: str) -> None:
        """Remove all chunks for a wiki page."""
        with self._tx() as c:
            c.execute("DELETE FROM wiki_chunks WHERE page_id = ?", (page_id,))
            c.execute("DELETE FROM chunk_meta WHERE page_id = ?", (page_id,))

    def search_wiki(self, query: str, limit: int = 10) -> list[dict]:
        """Search wiki chunks using FTS5 (or LIKE fallback)."""
        conn = self._get_conn()
        if self._fts5_available:
            try:
                rows = conn.execute(
                    """SELECT DISTINCT page_id,
                              snippet(wiki_chunks, 2, '<b>', '</b>', '...', 20) as snippet
                       FROM wiki_chunks
                       WHERE content MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (query, limit),
                ).fetchall()
                return [{"page_id": r["page_id"], "snippet": r["snippet"]} for r in rows]
            except sqlite3.OperationalError as e:
                logger.warning("FTS5 search error: %s", e)

        # Fallback: LIKE search
        rows = conn.execute(
            """SELECT DISTINCT page_id FROM wiki_chunks
               WHERE content LIKE ? LIMIT ?""",
            (f"%{query}%", limit),
        ).fetchall()
        return [{"page_id": r["page_id"], "snippet": ""} for r in rows]

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def record_telemetry(
        self,
        event_type: str,
        session_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Record a telemetry event for Phase 2 trigger analysis."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO _telemetry (event_type, session_id, data) VALUES (?, ?, ?)",
                (event_type, session_id, json.dumps(data or {})),
            )

    def query_telemetry_summary(self, days: int = 30) -> dict:
        """Dashboard query: % of sessions with content >100K chars."""
        conn = self._get_conn()
        total = conn.execute(
            """SELECT COUNT(*) FROM _telemetry
               WHERE event_type = 'compaction'
               AND recorded_at >= datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()[0]

        large = conn.execute(
            """SELECT COUNT(*) FROM _telemetry
               WHERE event_type = 'compaction'
               AND recorded_at >= datetime('now', ?)
               AND json_extract(data, '$.content_size') > 100000""",
            (f"-{days} days",),
        ).fetchone()[0]

        return {
            "total_compaction_events": total,
            "large_content_events": large,
            "large_content_pct": (large / total * 100) if total > 0 else 0.0,
            "days": days,
        }

    # ------------------------------------------------------------------
    # MigrationManager access
    # ------------------------------------------------------------------

    def get_migration_manager(self) -> MigrationManager:
        """Get a MigrationManager for this database."""
        return MigrationManager(self._get_conn())

    def schema_version(self) -> int:
        """Get the current schema version."""
        return MigrationManager(self._get_conn()).current_version()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
