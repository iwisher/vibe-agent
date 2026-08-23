"""Tests for SessionStore checkpoint management."""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

from vibe.harness.memory.session_store import SessionStore


class TestSessionStore:
    """Test SessionStore stale counting and cleanup."""

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = SessionStore(db_path=path)
        yield store
        os.unlink(path)

    def _set_updated_at(self, store: SessionStore, session_id: str, updated_at: str) -> None:
        """Directly update the updated_at timestamp for a checkpoint."""
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE session_checkpoints SET updated_at = ? WHERE session_id = ?",
                (updated_at, session_id),
            )
            conn.commit()

    def _make_old_timestamp(self, hours_ago: float = 48.0) -> str:
        """Return an ISO timestamp from N hours ago."""
        from datetime import timedelta

        return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()

    def _make_fresh_timestamp(self) -> str:
        """Return a current ISO timestamp."""
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # count_stale
    # ------------------------------------------------------------------

    def test_count_stale_fresh_only(self, store):
        """Fresh non-terminal checkpoints should not be counted as stale."""
        store.save_checkpoint("sess-1", "IDLE", messages=[])
        store.save_checkpoint("sess-2", "PLANNING", messages=[])

        assert store.count_stale(max_age_hours=24.0) == 0

    def test_count_stale_old_non_terminal(self, store):
        """Old non-terminal checkpoints should be counted as stale."""
        store.save_checkpoint("sess-1", "IDLE", messages=[])
        store.save_checkpoint("sess-2", "PLANNING", messages=[])

        old_ts = self._make_old_timestamp(hours_ago=48.0)
        self._set_updated_at(store, "sess-1", old_ts)
        self._set_updated_at(store, "sess-2", old_ts)

        assert store.count_stale(max_age_hours=24.0) == 2

    def test_count_stale_old_terminal_excluded(self, store):
        """Old terminal checkpoints should NOT be counted as stale."""
        for state in ("COMPLETED", "ERROR", "STOPPED", "INCOMPLETE"):
            store.save_checkpoint(f"sess-{state}", state, messages=[])
            old_ts = self._make_old_timestamp(hours_ago=48.0)
            self._set_updated_at(store, f"sess-{state}", old_ts)

        assert store.count_stale(max_age_hours=24.0) == 0

    def test_count_stale_mixed(self, store):
        """Mixed old and fresh checkpoints should count only old non-terminal."""
        # Fresh non-terminal
        store.save_checkpoint("fresh-idle", "IDLE", messages=[])

        # Old non-terminal
        store.save_checkpoint("old-idle", "IDLE", messages=[])
        self._set_updated_at(store, "old-idle", self._make_old_timestamp(hours_ago=48.0))

        # Old terminal (excluded)
        store.save_checkpoint("old-completed", "COMPLETED", messages=[])
        self._set_updated_at(store, "old-completed", self._make_old_timestamp(hours_ago=48.0))

        # Fresh terminal (excluded by age)
        store.save_checkpoint("fresh-completed", "COMPLETED", messages=[])

        assert store.count_stale(max_age_hours=24.0) == 1

    def test_count_stale_respects_max_age(self, store):
        """Count should respect the max_age_hours parameter."""
        store.save_checkpoint("sess-1", "IDLE", messages=[])
        # 12 hours old
        self._set_updated_at(store, "sess-1", self._make_old_timestamp(hours_ago=12.0))

        # With 24h threshold -> not stale
        assert store.count_stale(max_age_hours=24.0) == 0
        # With 6h threshold -> stale
        assert store.count_stale(max_age_hours=6.0) == 1

    # ------------------------------------------------------------------
    # count_all
    # ------------------------------------------------------------------

    def test_count_all_fresh_only(self, store):
        """Fresh checkpoints should not be counted."""
        store.save_checkpoint("sess-1", "IDLE", messages=[])
        store.save_checkpoint("sess-2", "COMPLETED", messages=[])

        assert store.count_all(max_age_hours=24.0) == 0

    def test_count_all_old_any_state(self, store):
        """Old checkpoints should be counted regardless of state."""
        states = ("IDLE", "PLANNING", "COMPLETED", "ERROR", "STOPPED", "INCOMPLETE")
        for i, state in enumerate(states):
            sid = f"sess-{i}"
            store.save_checkpoint(sid, state, messages=[])
            self._set_updated_at(store, sid, self._make_old_timestamp(hours_ago=48.0))

        assert store.count_all(max_age_hours=24.0) == len(states)

    def test_count_all_mixed_ages(self, store):
        """Mixed ages should count only old ones."""
        store.save_checkpoint("fresh", "IDLE", messages=[])
        store.save_checkpoint("old", "COMPLETED", messages=[])
        self._set_updated_at(store, "old", self._make_old_timestamp(hours_ago=48.0))

        assert store.count_all(max_age_hours=24.0) == 1

    def test_count_all_respects_max_age(self, store):
        """Count should respect the max_age_hours parameter."""
        store.save_checkpoint("sess-1", "IDLE", messages=[])
        self._set_updated_at(store, "sess-1", self._make_old_timestamp(hours_ago=72.0))

        assert store.count_all(max_age_hours=168.0) == 0
        assert store.count_all(max_age_hours=48.0) == 1

    # ------------------------------------------------------------------
    # cleanup_stale
    # ------------------------------------------------------------------

    def test_cleanup_stale_removes_old_non_terminal(self, store):
        """Should remove old non-terminal checkpoints."""
        store.save_checkpoint("old-idle", "IDLE", messages=[])
        self._set_updated_at(store, "old-idle", self._make_old_timestamp(hours_ago=48.0))

        removed = store.cleanup_stale(max_age_hours=24.0)
        assert removed == 1
        assert not store.has_checkpoint("old-idle")

    def test_cleanup_stale_keeps_old_terminal(self, store):
        """Should keep old terminal checkpoints."""
        for state in ("COMPLETED", "ERROR", "STOPPED", "INCOMPLETE"):
            store.save_checkpoint(f"sess-{state}", state, messages=[])
            self._set_updated_at(store, f"sess-{state}", self._make_old_timestamp(hours_ago=48.0))

        removed = store.cleanup_stale(max_age_hours=24.0)
        assert removed == 0
        for state in ("COMPLETED", "ERROR", "STOPPED", "INCOMPLETE"):
            assert store.has_checkpoint(f"sess-{state}")

    def test_cleanup_stale_keeps_fresh(self, store):
        """Should keep fresh non-terminal checkpoints."""
        store.save_checkpoint("fresh-idle", "IDLE", messages=[])

        removed = store.cleanup_stale(max_age_hours=24.0)
        assert removed == 0
        assert store.has_checkpoint("fresh-idle")

    def test_cleanup_stale_mixed(self, store):
        """Should remove only old non-terminal checkpoints."""
        store.save_checkpoint("fresh-idle", "IDLE", messages=[])
        store.save_checkpoint("old-idle", "IDLE", messages=[])
        self._set_updated_at(store, "old-idle", self._make_old_timestamp(hours_ago=48.0))
        store.save_checkpoint("old-completed", "COMPLETED", messages=[])
        self._set_updated_at(store, "old-completed", self._make_old_timestamp(hours_ago=48.0))

        removed = store.cleanup_stale(max_age_hours=24.0)
        assert removed == 1
        assert store.has_checkpoint("fresh-idle")
        assert not store.has_checkpoint("old-idle")
        assert store.has_checkpoint("old-completed")

    # ------------------------------------------------------------------
    # cleanup_all
    # ------------------------------------------------------------------

    def test_cleanup_all_removes_old_any_state(self, store):
        """Should remove old checkpoints regardless of state."""
        states = ("IDLE", "COMPLETED", "ERROR")
        for i, state in enumerate(states):
            sid = f"sess-{i}"
            store.save_checkpoint(sid, state, messages=[])
            self._set_updated_at(store, sid, self._make_old_timestamp(hours_ago=48.0))

        removed = store.cleanup_all(max_age_hours=24.0)
        assert removed == 3
        for i in range(len(states)):
            assert not store.has_checkpoint(f"sess-{i}")

    def test_cleanup_all_keeps_fresh(self, store):
        """Should keep fresh checkpoints."""
        store.save_checkpoint("fresh-idle", "IDLE", messages=[])
        store.save_checkpoint("fresh-completed", "COMPLETED", messages=[])

        removed = store.cleanup_all(max_age_hours=24.0)
        assert removed == 0
        assert store.has_checkpoint("fresh-idle")
        assert store.has_checkpoint("fresh-completed")

    def test_cleanup_all_mixed(self, store):
        """Should remove only old checkpoints across all states."""
        store.save_checkpoint("fresh-idle", "IDLE", messages=[])
        store.save_checkpoint("old-completed", "COMPLETED", messages=[])
        self._set_updated_at(store, "old-completed", self._make_old_timestamp(hours_ago=48.0))
        store.save_checkpoint("old-error", "ERROR", messages=[])
        self._set_updated_at(store, "old-error", self._make_old_timestamp(hours_ago=48.0))

        removed = store.cleanup_all(max_age_hours=24.0)
        assert removed == 2
        assert store.has_checkpoint("fresh-idle")
        assert not store.has_checkpoint("old-completed")
        assert not store.has_checkpoint("old-error")

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

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
        store.save_checkpoint(
            "sess-002",
            "PROCESSING",
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        )

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
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_checkpoints_updated'"
            ).fetchall()
            assert len(indexes) == 1
