"""Tests for TraceStore."""

from pathlib import Path

import pytest

from vibe.harness.memory.trace_store import TraceStore

try:
    import sentence_transformers  # noqa: F401
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False


def test_log_and_retrieve_similar_sessions(tmp_path):
    db_path = tmp_path / "traces.db"
    store = TraceStore(db_path=str(db_path))

    store.log_session(
        session_id="sess-1",
        messages=[
            {"role": "user", "content": "how do I write rust code"},
            {"role": "assistant", "content": "here is some rust"},
        ],
        tool_results=[],
        success=True,
        model="test-model",
    )

    store.log_session(
        session_id="sess-2",
        messages=[
            {"role": "user", "content": "what is the weather"},
        ],
        tool_results=[],
        success=True,
        model="other-model",
    )

    similar = store.get_similar_sessions("help with rust programming", limit=5)
    assert len(similar) >= 1
    assert any(s["id"] == "sess-1" for s in similar)
    assert not any(s["id"] == "sess-2" for s in similar)


def test_get_similar_sessions_empty_query(tmp_path):
    db_path = tmp_path / "traces.db"
    store = TraceStore(db_path=str(db_path))
    assert store.get_similar_sessions("a") == []


@pytest.mark.skipif(not HAS_SENTENCE_TRANSFORMERS, reason="sentence-transformers not installed")
def test_vector_similarity_search(tmp_path):
    db_path = tmp_path / "traces.db"
    store = TraceStore(db_path=str(db_path))

    # Log sessions with distinct topics
    store.log_session(
        session_id="rust-sess",
        messages=[{"role": "user", "content": "how to write a rust macro"}],
        tool_results=[],
        success=True,
        model="m1",
    )
    store.log_session(
        session_id="weather-sess",
        messages=[{"role": "user", "content": "what is the temperature in Tokyo"}],
        tool_results=[],
        success=True,
        model="m2",
    )

    # Search for something related to rust
    results = store.get_similar_sessions_vector("programming in rust language", limit=5)
    assert len(results) >= 1
    assert results[0]["id"] == "rust-sess"
    assert "score" in results[0]
    assert results[0]["score"] > 0

    # Test top-level get_similar_sessions uses vector search
    similar = store.get_similar_sessions("rust programming", limit=5)
    assert len(similar) >= 1
    assert similar[0]["id"] == "rust-sess"


def test_vector_search_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "traces.db"
    store = TraceStore(db_path=str(db_path))

    # Mock _get_embedding to return None to force fallback
    monkeypatch.setattr(store, "_get_embedding", lambda x: None)

    store.log_session(
        session_id="keyword-sess",
        messages=[{"role": "user", "content": "the quick brown fox"}],
        tool_results=[],
        success=True,
        model="m1",
    )

    # Should fallback to keyword search
    similar = store.get_similar_sessions("quick brown fox", limit=5)
    assert len(similar) == 1
    assert similar[0]["id"] == "keyword-sess"
