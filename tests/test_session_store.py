"""Tests for SessionStore checkpoint persistence."""

import json
import os
import sqlite3
import tempfile

import pytest

from vibe.harness.memory.session_store import SessionStore


@pytest.fixture
def store():
    """Create a SessionStore backed by a temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = SessionStore(db_path=db_path)
    yield store
    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


class TestSessionStore:
    """Unit tests for SessionStore CRUD operations."""

    def test_save_and_load_checkpoint(self, store):
        """Roundtrip: save a checkpoint and load it back."""
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there", "model_version": "gpt-4"},
        ]
        plan = {"selected_tool_names": ["bash"], "system_prompt_append": "test prompt"}

        store.save_checkpoint(
            session_id="sess-001",
            state="PLANNING",
            messages=messages,
            plan_result=plan,
            iteration=3,
            feedback_retries=1,
            model="gpt-4",
        )

        cp = store.load_checkpoint("sess-001")
        assert cp is not None
        assert cp["session_id"] == "sess-001"
        assert cp["state"] == "PLANNING"
        assert cp["iteration"] == 3
        assert cp["feedback_retries"] == 1
        assert cp["model"] == "gpt-4"
        assert len(cp["messages"]) == 2
        assert cp["messages"][1]["model_version"] == "gpt-4"
        assert cp["plan_result"]["selected_tool_names"] == ["bash"]

    def test_update_existing_checkpoint(self, store):
        """Saving twice with same session_id updates the checkpoint."""
        store.save_checkpoint("sess-002", "PLANNING", [{"role": "user", "content": "hi"}])
        store.save_checkpoint("sess-002", "PROCESSING", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}])

        cp = store.load_checkpoint("sess-002")
        assert cp["state"] == "PROCESSING"
        assert len(cp["messages"]) == 2

    def test_load_missing_checkpoint(self, store):
        """Loading a non-existent session returns None."""
        assert store.load_checkpoint("nonexistent") is None

    def test_list_incomplete_ordering(self, store):
        """list_incomplete returns sessions ordered by updated_at DESC."""
        store.save_checkpoint("sess-a", "PLANNING", [{"role": "user", "content": "a"}])
        store.save_checkpoint("sess-b", "PROCESSING", [{"role": "user", "content": "b"}])
        store.save_checkpoint("sess-c", "TOOL_EXECUTION", [{"role": "user", "content": "c"}])

        sessions = store.list_incomplete(limit=10)
        assert len(sessions) == 3
        # Most recently updated first (sess-c was saved last)
        ids = [s["session_id"] for s in sessions]
        assert ids[0] == "sess-c"
        assert ids[1] == "sess-b"
        assert ids[2] == "sess-a"

    def test_list_incomplete_limit(self, store):
        """list_incomplete respects the limit parameter."""
        for i in range(5):
            store.save_checkpoint(f"sess-{i}", "PLANNING", [{"role": "user", "content": str(i)}])

        sessions = store.list_incomplete(limit=2)
        assert len(sessions) == 2

    def test_delete_checkpoint(self, store):
        """delete_checkpoint removes the checkpoint."""
        store.save_checkpoint("sess-del", "PLANNING", [{"role": "user", "content": "x"}])
        assert store.has_checkpoint("sess-del") is True

        deleted = store.delete_checkpoint("sess-del")
        assert deleted is True
        assert store.has_checkpoint("sess-del") is False
        assert store.load_checkpoint("sess-del") is None

    def test_delete_missing_checkpoint(self, store):
        """delete_checkpoint returns False when session doesn't exist."""
        assert store.delete_checkpoint("nonexistent") is False

    def test_has_checkpoint(self, store):
        """has_checkpoint correctly reports existence."""
        assert store.has_checkpoint("sess-x") is False
        store.save_checkpoint("sess-x", "PLANNING", [])
        assert store.has_checkpoint("sess-x") is True

    def test_count_checkpoints(self, store):
        """count_checkpoints returns the total number of checkpoints."""
        assert store.count_checkpoints() == 0
        store.save_checkpoint("sess-1", "PLANNING", [])
        assert store.count_checkpoints() == 1
        store.save_checkpoint("sess-2", "PROCESSING", [])
        assert store.count_checkpoints() == 2
        store.delete_checkpoint("sess-1")
        assert store.count_checkpoints() == 1

    def test_messages_with_tool_calls(self, store):
        """Messages containing tool_calls serialize correctly."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc-1", "function": {"name": "bash", "arguments": "{}"}}],
            },
            {"role": "tool", "content": "output", "tool_call_id": "tc-1"},
        ]
        store.save_checkpoint("sess-tools", "TOOL_EXECUTION", messages)

        cp = store.load_checkpoint("sess-tools")
        assert len(cp["messages"]) == 2
        assert cp["messages"][0]["tool_calls"][0]["id"] == "tc-1"
        assert cp["messages"][1]["tool_call_id"] == "tc-1"

    def test_plan_result_optional(self, store):
        """Checkpoint without plan_result stores NULL."""
        store.save_checkpoint("sess-no-plan", "PLANNING", [{"role": "user", "content": "hi"}])
        cp = store.load_checkpoint("sess-no-plan")
        assert cp["plan_result"] is None

    def test_concurrent_updates(self, store):
        """Rapid sequential updates don't corrupt the checkpoint."""
        for i in range(50):
            store.save_checkpoint(
                "sess-race",
                "PROCESSING",
                [{"role": "user", "content": f"msg-{i}"}],
                iteration=i,
            )

        cp = store.load_checkpoint("sess-race")
        assert cp is not None
        assert cp["iteration"] == 49
        assert cp["messages"][0]["content"] == "msg-49"

    def test_db_schema_created(self, store):
        """The session_checkpoints table and index are created on init."""
        with sqlite3.connect(store.db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_checkpoints'"
            ).fetchall()
            assert len(tables) == 1

            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_checkpoints_updated'"
            ).fetchall()
            assert len(indexes) == 1
