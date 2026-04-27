"""Unit tests for SharedMemoryDB — schema versioning, FTS5, migration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vibe.memory.shared_db import SharedMemoryDB, MigrationManager, SCHEMA_VERSION


@pytest.fixture
def tmp_db(tmp_path):
    return SharedMemoryDB(db_path=tmp_path / "memory.db")


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------


def test_db_creates_file(tmp_path):
    db = SharedMemoryDB(db_path=tmp_path / "mem.db")
    assert (tmp_path / "mem.db").exists()
    db.close()


def test_schema_version_is_current(tmp_db):
    assert tmp_db.schema_version() == SCHEMA_VERSION
    tmp_db.close()


def test_db_creates_all_tables(tmp_db):
    conn = tmp_db._get_conn()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "_schema_version" in tables
    assert "sessions" in tables
    assert "evals" in tables
    assert "chunk_meta" in tables
    assert "_telemetry" in tables
    tmp_db.close()


def test_db_creates_wiki_chunks_table(tmp_db):
    """wiki_chunks must exist (FTS5 or regular table fallback)."""
    conn = tmp_db._get_conn()
    tables_and_views = set(
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        )
    )
    assert "wiki_chunks" in tables_and_views or "wiki_chunks_content" in tables_and_views
    tmp_db.close()


# ---------------------------------------------------------------------------
# Wiki chunk sync
# ---------------------------------------------------------------------------


class FakeWikiPage:
    def __init__(self, page_id, content, path=None):
        self.id = page_id
        self.content = content
        from pathlib import Path
        self.path = path or Path("/tmp/test.md")


def test_sync_wiki_page_inserts_chunks(tmp_db):
    page = FakeWikiPage("page-001", "Short content for testing FTS5 indexing.")
    tmp_db.sync_wiki_page(page)

    results = tmp_db.search_wiki("content")
    page_ids = [r["page_id"] for r in results]
    assert "page-001" in page_ids
    tmp_db.close()


def test_sync_wiki_page_skips_if_content_unchanged(tmp_db):
    """sync_wiki_page should skip re-indexing when content hash unchanged."""
    page = FakeWikiPage("page-unchanged", "Exact same content.")
    tmp_db.sync_wiki_page(page)

    conn = tmp_db._get_conn()
    count_before = conn.execute("SELECT COUNT(*) FROM chunk_meta WHERE page_id = ?", ("page-unchanged",)).fetchone()[0]
    assert count_before > 0

    # Sync again with same content — should be a no-op
    tmp_db.sync_wiki_page(page)
    count_after = conn.execute("SELECT COUNT(*) FROM chunk_meta WHERE page_id = ?", ("page-unchanged",)).fetchone()[0]
    assert count_after == count_before  # No new chunks
    tmp_db.close()


def test_sync_wiki_page_updates_on_content_change(tmp_db):
    page = FakeWikiPage("page-changing", "Initial content version 1.")
    tmp_db.sync_wiki_page(page)

    # Update content
    page.content = "Updated content version 2 with new text."
    tmp_db.sync_wiki_page(page)

    results = tmp_db.search_wiki("Updated")
    page_ids = [r["page_id"] for r in results]
    assert "page-changing" in page_ids
    tmp_db.close()


def test_delete_wiki_page(tmp_db):
    page = FakeWikiPage("page-delete", "Content to be deleted.")
    tmp_db.sync_wiki_page(page)

    results_before = tmp_db.search_wiki("deleted")
    # Note: may or may not match depending on FTS5 tokenization

    tmp_db.delete_wiki_page("page-delete")
    conn = tmp_db._get_conn()
    chunks = conn.execute("SELECT COUNT(*) FROM chunk_meta WHERE page_id = ?", ("page-delete",)).fetchone()[0]
    assert chunks == 0
    tmp_db.close()


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def test_record_telemetry(tmp_db):
    tmp_db.record_telemetry("compaction", session_id="sess-123", data={"content_size": 5000})
    conn = tmp_db._get_conn()
    row = conn.execute("SELECT event_type, session_id FROM _telemetry LIMIT 1").fetchone()
    assert row["event_type"] == "compaction"
    assert row["session_id"] == "sess-123"
    tmp_db.close()


def test_telemetry_summary(tmp_db):
    tmp_db.record_telemetry("compaction", data={"content_size": 200000})
    summary = tmp_db.query_telemetry_summary(days=30)
    assert "total_compaction_events" in summary
    assert "large_content_pct" in summary
    tmp_db.close()


# ---------------------------------------------------------------------------
# MigrationManager
# ---------------------------------------------------------------------------


def test_migration_manager_version(tmp_db):
    mgr = tmp_db.get_migration_manager()
    assert mgr.current_version() == SCHEMA_VERSION
    tmp_db.close()


def test_migration_manager_no_double_apply(tmp_db):
    """Running migrations twice should be idempotent."""
    mgr = tmp_db.get_migration_manager()
    mgr.run(SCHEMA_VERSION)  # Already at current — should be no-op
    assert mgr.current_version() == SCHEMA_VERSION
    tmp_db.close()


def test_migrate_from_traces_db_missing_file(tmp_db, tmp_path):
    mgr = tmp_db.get_migration_manager()
    count = mgr.migrate_from_traces_db(tmp_path / "nonexistent.db")
    assert count == 0
    tmp_db.close()


def test_migrate_from_evals_db_missing_file(tmp_db, tmp_path):
    mgr = tmp_db.get_migration_manager()
    count = mgr.migrate_from_evals_db(tmp_path / "nonexistent.db")
    assert count == 0
    tmp_db.close()
