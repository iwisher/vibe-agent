"""Tests for WikiCompiler — Phase 7 trace compilation with pending/ review."""

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.memory.compiler import CompilationSummary, WikiCompiler
from vibe.memory.wiki import LLMWiki
from vibe.harness.memory.trace_store import TraceStore


@pytest.fixture
def tmp_trace_store(tmp_path):
    """Create a temporary SQLite trace store with sample sessions."""
    db_path = tmp_path / "traces.db"
    store = TraceStore(db_path=str(db_path))

    # Insert a session from 1 hour ago
    now = datetime.now(timezone.utc).isoformat()
    session_id = "sess-001"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO sessions (id, start_time, end_time, success, model, error) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, now, now, 1, "test-model", None),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?, ?, ?, ?, ?)",
            (session_id, "user", "Docker Compose supports network_mode host", None, now),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?, ?, ?, ?, ?)",
            (session_id, "assistant", "Yes, you can use network_mode: host in Docker Compose.", None, now),
        )

    return store, session_id


@pytest.fixture
def tmp_wiki(tmp_path):
    """Create a temporary main wiki."""
    return LLMWiki(base_path=tmp_path / "wiki")


@pytest.fixture
def mock_llm_client():
    """Mock LLM client that returns structured knowledge extraction JSON."""
    client = MagicMock()
    response = MagicMock()
    response.content = json.dumps([
        {
            "title": "Docker Compose Network Mode",
            "content": "Docker Compose supports `network_mode: host` to share the host's network namespace.",
            "tags": ["docker", "networking", "compose"],
            "citations": [{"session": "sess-001", "message_index": 0}],
        }
    ])
    client.complete = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_compiler_creates_pending_page(tmp_trace_store, tmp_wiki, mock_llm_client):
    """Compiler should extract knowledge and create a pending wiki page."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
        pending_wiki_path=tmp_wiki.base_path.parent / "pending",
    )

    summary = await compiler.compile_recent(hours=24)

    assert summary.sessions_scanned >= 1
    assert summary.items_extracted >= 1
    assert summary.pages_created >= 1
    assert summary.errors == 0

    # Verify pending page exists
    pending = await compiler.list_pending()
    assert len(pending) >= 1
    assert any("Docker Compose" in p.title for p in pending)


@pytest.mark.asyncio
async def test_compiler_deduplicates_by_slug(tmp_trace_store, tmp_wiki, mock_llm_client):
    """Compiler should merge content instead of creating duplicate pending pages."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
        pending_wiki_path=tmp_wiki.base_path.parent / "pending",
    )

    # Run twice with same session data
    summary1 = await compiler.compile_recent(hours=24)
    summary2 = await compiler.compile_recent(hours=24)

    # Should merge, not create duplicate titles
    pending = await compiler.list_pending()
    titles = [p.title for p in pending]
    assert titles.count("Docker Compose Network Mode") == 1


@pytest.mark.asyncio
async def test_compiler_empty_sessions(tmp_wiki, mock_llm_client):
    """Compiler should handle empty trace store gracefully."""
    db_path = tmp_wiki.base_path.parent / "empty_traces.db"
    store = TraceStore(db_path=str(db_path))

    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
        pending_wiki_path=tmp_wiki.base_path.parent / "pending",
    )

    summary = await compiler.compile_recent(hours=24)
    assert summary.sessions_scanned == 0
    assert summary.pages_created == 0


@pytest.mark.asyncio
async def test_approve_page_moves_to_main_wiki(tmp_trace_store, tmp_wiki, mock_llm_client):
    """Approving a pending page should copy it to main wiki and delete from pending."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
        pending_wiki_path=tmp_wiki.base_path.parent / "pending",
    )

    await compiler.compile_recent(hours=24)
    pending_before = await compiler.list_pending()
    assert len(pending_before) >= 1
    page = pending_before[0]

    main_page = await compiler.approve_page(page.id)

    # Main wiki should have verified page
    assert main_page.status == "verified"
    assert main_page.title == page.title

    # Pending should be gone
    pending_after = await compiler.list_pending()
    assert not any(p.id == page.id for p in pending_after)


@pytest.mark.asyncio
async def test_reject_page_deletes_from_pending(tmp_trace_store, tmp_wiki, mock_llm_client):
    """Rejecting a pending page should delete it from pending."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
        pending_wiki_path=tmp_wiki.base_path.parent / "pending",
    )

    await compiler.compile_recent(hours=24)
    pending_before = await compiler.list_pending()
    assert len(pending_before) >= 1
    page = pending_before[0]

    await compiler.reject_page(page.id)

    pending_after = await compiler.list_pending()
    assert not any(p.id == page.id for p in pending_after)


@pytest.mark.asyncio
async def test_review_all_auto_approve(tmp_trace_store, tmp_wiki, mock_llm_client):
    """review_all with auto_approve=True should promote all pending pages."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
        pending_wiki_path=tmp_wiki.base_path.parent / "pending",
    )

    await compiler.compile_recent(hours=24)
    pending = await compiler.list_pending()
    assert len(pending) >= 1

    result = await compiler.review_all(auto_approve=True)
    assert result["approved"] == len(pending)
    assert result["rejected"] == 0

    # All pending should be gone
    pending_after = await compiler.list_pending()
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_compiler_get_session_messages(tmp_trace_store, tmp_wiki, mock_llm_client):
    """_get_session_messages should retrieve messages from SQLite trace store."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
    )

    messages = compiler._get_session_messages(session_id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert "Docker Compose" in messages[0].content


@pytest.mark.asyncio
async def test_compiler_get_recent_sessions(tmp_trace_store, tmp_wiki, mock_llm_client):
    """_get_recent_sessions should return sessions within time window."""
    store, session_id = tmp_trace_store
    compiler = WikiCompiler(
        trace_store=store,
        wiki=tmp_wiki,
        llm_client=mock_llm_client,
    )

    sessions = compiler._get_recent_sessions(hours=24)
    assert len(sessions) >= 1
    assert any(s["id"] == session_id for s in sessions)

    # Very short window should return nothing
    sessions_old = compiler._get_recent_sessions(hours=0)
    assert len(sessions_old) == 0
