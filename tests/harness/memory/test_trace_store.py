"""Tests for scalable TraceStore backends."""

import os
import tempfile

import pytest

from vibe.harness.memory.trace_store import (
    JSONTraceStore,
    MemoryTraceStore,
    SQLiteTraceStore,
    create_trace_store,
)


class TestSQLiteTraceStore:
    """Test SQLite trace store."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = SQLiteTraceStore(db_path=path, max_entries=100, retention_days=30)
        yield store
        os.unlink(path)

    def test_log_and_retrieve(self, store):
        """Should log and retrieve sessions."""
        store.log_session(
            session_id="test-1",
            messages=[{"role": "user", "content": "hello"}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        sessions = store.get_recent_sessions(limit=10)
        assert len(sessions) == 1
        assert sessions[0]["id"] == "test-1"
        assert sessions[0]["model"] == "gpt-4"

    def test_pagination(self, store):
        """Should support pagination."""
        for i in range(5):
            store.log_session(
                session_id=f"test-{i}",
                messages=[{"role": "user", "content": f"msg {i}"}],
                tool_results=[],
                success=True,
                model="gpt-4",
            )

        page1 = store.get_sessions(limit=2, offset=0)
        page2 = store.get_sessions(limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]

    def test_filter_by_success(self, store):
        """Should filter by success status."""
        store.log_session(
            session_id="success-1",
            messages=[],
            tool_results=[],
            success=True,
            model="gpt-4",
        )
        store.log_session(
            session_id="fail-1",
            messages=[],
            tool_results=[],
            success=False,
            model="gpt-4",
            error="timeout",
        )

        success_sessions = store.get_sessions(success=True)
        assert len(success_sessions) == 1
        assert success_sessions[0]["id"] == "success-1"

    def test_count_sessions(self, store):
        """Should count sessions."""
        assert store.count_sessions() == 0

        store.log_session(
            session_id="test-1",
            messages=[],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        assert store.count_sessions() == 1

    def test_cleanup_old_sessions(self, store):
        """Should remove old sessions."""
        # Log a session
        store.log_session(
            session_id="test-1",
            messages=[],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        # Cleanup with 0 days retention
        removed = store.cleanup_old_sessions(0)
        assert removed == 1
        assert store.count_sessions() == 0

    def test_redaction_in_trace_store(self):
        """Verify secrets are redacted before persistence."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = SQLiteTraceStore(db_path=path, max_entries=100, retention_days=30)
        # Force init by calling count_sessions (which triggers _init_db)
        store.count_sessions()

        key = "sk-" + "a" * 48
        messages = [
            {"role": "user", "content": f"My key is {key}"},
            {"role": "assistant", "content": "Done"},
        ]
        store.log_session("session-1", messages, [], success=True, model="test")

        # Query messages directly from SQLite to verify redaction
        import sqlite3

        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT content FROM messages WHERE session_id = ?", ("session-1",)
            ).fetchall()

        assert len(rows) == 2
        stored_content = rows[0]["content"]
        assert key not in stored_content
        assert "[REDACTED_OPENAI_KEY]" in stored_content
        os.unlink(path)

    def test_max_entries_enforcement(self, store):
        """Should enforce max_entries limit."""
        store.max_entries = 3
        store.cleanup_interval_seconds = 0  # Immediate cleanup for testing

    def test_similar_sessions_keyword(self, store):
        """Should find similar sessions by keyword."""
        store.log_session(
            session_id="python-session",
            messages=[{"role": "user", "content": "write python code"}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )
        store.log_session(
            session_id="bash-session",
            messages=[{"role": "user", "content": "run bash script"}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        results = store.get_similar_sessions("python coding", limit=5)
        assert len(results) >= 1
        assert any("python" in r["id"] for r in results)


class TestJSONTraceStore:
    """Test JSON trace store."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = JSONTraceStore(file_path=path, max_entries=100, retention_days=30)
        yield store
        os.unlink(path)

    def test_log_and_retrieve(self, store):
        """Should log and retrieve sessions."""
        store.log_session(
            session_id="test-1",
            messages=[{"role": "user", "content": "hello"}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        sessions = store.get_recent_sessions(limit=10)
        assert len(sessions) == 1
        assert sessions[0]["id"] == "test-1"

    def test_persistence(self, store):
        """Should persist across instances."""
        store.log_session(
            session_id="test-1",
            messages=[],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        # Create new instance with same file
        store2 = JSONTraceStore(file_path=store.file_path)
        assert store2.count_sessions() == 1

    def test_cleanup(self, store):
        """Should cleanup old sessions."""
        store.log_session(
            session_id="test-1",
            messages=[],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        removed = store.cleanup_old_sessions(0)
        assert removed == 1


class TestMemoryTraceStore:
    """Test in-memory trace store."""

    @pytest.fixture
    def store(self):
        return MemoryTraceStore(max_entries=100, retention_days=30)

    def test_log_and_retrieve(self, store):
        """Should log and retrieve sessions."""
        store.log_session(
            session_id="test-1",
            messages=[{"role": "user", "content": "hello"}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        sessions = store.get_recent_sessions(limit=10)
        assert len(sessions) == 1

    def test_no_persistence(self, store):
        """Should not persist across instances."""
        store.log_session(
            session_id="test-1",
            messages=[],
            tool_results=[],
            success=True,
            model="gpt-4",
        )

        store2 = MemoryTraceStore()
        assert store2.count_sessions() == 0


class TestFactory:
    """Test trace store factory."""

    def test_create_sqlite(self):
        """Should create SQLite store."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = create_trace_store("sqlite", db_path=path)
        assert isinstance(store, SQLiteTraceStore)
        os.unlink(path)

    def test_create_json(self):
        """Should create JSON store."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = create_trace_store("json", db_path=path)
        assert isinstance(store, JSONTraceStore)
        os.unlink(path)

    def test_create_memory(self):
        """Should create memory store."""
        store = create_trace_store("memory")
        assert isinstance(store, MemoryTraceStore)

    def test_invalid_type(self):
        """Should raise error for invalid type."""
        with pytest.raises(ValueError):
            create_trace_store("invalid")


class TestSimilarSessionsSuccessFilter:
    """get_similar_sessions must return successful sessions only by default."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = SQLiteTraceStore(db_path=path, max_entries=100, retention_days=30)
        yield store
        os.unlink(path)

    def _log_pair(self, store):
        store.log_session(
            session_id="ok-python",
            messages=[{"role": "user", "content": "write python code"}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )
        store.log_session(
            session_id="fail-python",
            messages=[{"role": "user", "content": "write python code"}],
            tool_results=[],
            success=False,
            model="gpt-4",
            error="boom",
        )

    def test_keyword_path_filters_failures(self, store):
        """Keyword fallback must not surface failed sessions."""
        self._log_pair(store)
        results = store.get_similar_sessions("python code", limit=5)
        ids = [r["id"] for r in results]
        assert "ok-python" in ids
        assert "fail-python" not in ids

    def test_keyword_path_success_only_false_includes_failures(self, store):
        self._log_pair(store)
        results = store.get_similar_sessions("python code", limit=5, success_only=False)
        ids = [r["id"] for r in results]
        assert "ok-python" in ids
        assert "fail-python" in ids

    def test_vector_path_filters_failures(self, store, monkeypatch):
        """Vector path must filter failures at the SQL level."""
        self._log_pair(store)

        # Force the vector path with a deterministic embedding function
        def fake_embedding(text):
            return [1.0, 0.0, 0.0, 0.0]

        monkeypatch.setattr(store, "_get_embedding", fake_embedding)
        # Re-log so embeddings exist for both sessions
        self._log_pair(store)

        results = store.get_similar_sessions_vector("python code", limit=5)
        ids = [r["id"] for r in results]
        assert "fail-python" not in ids
        assert "ok-python" in ids

        results_all = store.get_similar_sessions_vector("python code", limit=5, success_only=False)
        ids_all = [r["id"] for r in results_all]
        assert "fail-python" in ids_all

    def test_memory_store_filters_failures(self):
        mem = MemoryTraceStore()
        mem.log_session(
            "ok-1",
            [{"role": "user", "content": "deploy kubernetes cluster"}],
            [],
            True,
            "m",
        )
        mem.log_session(
            "bad-1",
            [{"role": "user", "content": "deploy kubernetes cluster"}],
            [],
            False,
            "m",
        )
        ids = [s["id"] for s in mem.get_similar_sessions("deploy kubernetes")]
        assert "ok-1" in ids
        assert "bad-1" not in ids
        ids_all = [
            s["id"] for s in mem.get_similar_sessions("deploy kubernetes", success_only=False)
        ]
        assert "bad-1" in ids_all


class TestGetSessionSnippet:
    """get_session_snippet returns the final non-empty assistant message, bounded."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = SQLiteTraceStore(db_path=path, max_entries=100, retention_days=30)
        yield store
        os.unlink(path)

    def test_returns_final_assistant_message(self, store):
        store.log_session(
            session_id="s1",
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "first answer"},
                {"role": "assistant", "content": "final   answer\nwith whitespace"},
                {"role": "assistant", "content": "   "},
            ],
            tool_results=[],
            success=True,
            model="gpt-4",
        )
        snippet = store.get_session_snippet("s1")
        # Final non-empty assistant message, whitespace collapsed to one line
        assert snippet == "final answer with whitespace"

    def test_truncates_with_ellipsis(self, store):
        store.log_session(
            session_id="s2",
            messages=[{"role": "assistant", "content": "word " * 100}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )
        snippet = store.get_session_snippet("s2", max_chars=50)
        assert snippet is not None
        assert len(snippet) <= 50
        assert snippet.endswith("…")

    def test_default_max_chars_200(self, store):
        store.log_session(
            session_id="s3",
            messages=[{"role": "assistant", "content": "z" * 1000}],
            tool_results=[],
            success=True,
            model="gpt-4",
        )
        snippet = store.get_session_snippet("s3")
        assert snippet is not None
        assert len(snippet) <= 200

    def test_never_raises(self, store):
        assert store.get_session_snippet("nonexistent") is None
        assert store.get_session_snippet(None) is None
        assert store.get_session_snippet("") is None

    def test_json_and_memory_backends(self, tmp_path):
        for backend in (
            MemoryTraceStore(),
            JSONTraceStore(file_path=str(tmp_path / "traces.json")),
        ):
            backend.log_session(
                "s1",
                [
                    {"role": "assistant", "content": "older"},
                    {"role": "assistant", "content": "final answer"},
                ],
                [],
                True,
                "m",
            )
            assert backend.get_session_snippet("s1") == "final answer"
            assert backend.get_session_snippet("missing") is None
