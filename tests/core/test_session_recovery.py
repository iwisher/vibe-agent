"""Tests for durable session recovery."""

import time

from vibe.core.session_recovery import (
    RecoveryStatus,
    SessionCheckpoint,
    SessionRecoveryManager,
)


class MockSessionStore:
    def __init__(self):
        self._checkpoints: dict[str, dict] = {}

    def save_checkpoint(self, **kwargs):
        session_id = kwargs["session_id"]
        self._checkpoints[session_id] = kwargs

    def load_checkpoint(self, session_id: str):
        return self._checkpoints.get(session_id)

    def delete_checkpoint(self, session_id: str):
        return self._checkpoints.pop(session_id, None) is not None

    def list_incomplete(self, limit: int = 20):
        return [
            {"session_id": sid, "state": cp["state"], "updated_at": time.time()}
            for sid, cp in self._checkpoints.items()
        ]


class TestSessionCheckpoint:
    def test_to_dict(self):
        cp = SessionCheckpoint(
            session_id="sess-1",
            state="PROCESSING",
            messages=[{"role": "user", "content": "hello"}],
            plan_result=None,
            iteration=3,
            feedback_retries=0,
            model="gpt-4",
        )
        d = cp.to_dict()
        assert d["session_id"] == "sess-1"
        assert d["state"] == "PROCESSING"
        assert d["iteration"] == 3

    def test_from_dict(self):
        d = {
            "session_id": "sess-1",
            "state": "PROCESSING",
            "messages": [{"role": "user", "content": "hello"}],
            "plan_result": None,
            "iteration": 3,
            "feedback_retries": 0,
            "model": "gpt-4",
            "metadata": {},
            "created_at": time.time(),
        }
        cp = SessionCheckpoint.from_dict(d)
        assert cp.session_id == "sess-1"
        assert cp.iteration == 3


class TestSessionRecoveryManager:
    def test_save_and_recover(self):
        store = MockSessionStore()
        mgr = SessionRecoveryManager(store)

        mgr.save_checkpoint(
            session_id="sess-1",
            state="PROCESSING",
            messages=[{"role": "user", "content": "hello"}],
            iteration=3,
        )

        status, checkpoint = mgr.recover("sess-1")
        assert status == RecoveryStatus.RESTORED
        assert checkpoint is not None
        assert checkpoint.iteration == 3

    def test_no_checkpoint(self):
        store = MockSessionStore()
        mgr = SessionRecoveryManager(store)

        status, checkpoint = mgr.recover("nonexistent")
        assert status == RecoveryStatus.NO_CHECKPOINT
        assert checkpoint is None

    def test_expired_checkpoint(self):
        store = MockSessionStore()
        mgr = SessionRecoveryManager(store, checkpoint_ttl_seconds=0.5)

        # Create checkpoint with explicit old timestamp
        old_time = time.time() - 2.0
        cp = SessionCheckpoint(
            session_id="sess-1",
            state="PROCESSING",
            messages=[{"role": "user", "content": "hello"}],
            plan_result=None,
            iteration=0,
            feedback_retries=0,
            model=None,
            created_at=old_time,
        )
        store.save_checkpoint(**cp.to_dict())

        status, checkpoint = mgr.recover("sess-1")
        assert status == RecoveryStatus.EXPIRED
        assert checkpoint is not None

    def test_list_recoverable(self):
        store = MockSessionStore()
        mgr = SessionRecoveryManager(store)

        mgr.save_checkpoint(
            session_id="sess-1",
            state="PROCESSING",
            messages=[{"role": "user", "content": "hello"}],
        )
        mgr.save_checkpoint(
            session_id="sess-2",
            state="ERROR",
            messages=[{"role": "user", "content": "hello"}],
        )

        recoverable = mgr.list_recoverable()
        assert len(recoverable) == 2
        assert "sess-1" in recoverable
        assert "sess-2" in recoverable

    def test_delete_checkpoint(self):
        store = MockSessionStore()
        mgr = SessionRecoveryManager(store)

        mgr.save_checkpoint(
            session_id="sess-1",
            state="PROCESSING",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert mgr.delete_checkpoint("sess-1") is True
        status, _ = mgr.recover("sess-1")
        assert status == RecoveryStatus.NO_CHECKPOINT

    def test_save_without_store(self):
        mgr = SessionRecoveryManager(None)
        # Should not raise
        mgr.save_checkpoint(
            session_id="sess-1",
            state="PROCESSING",
            messages=[{"role": "user", "content": "hello"}],
        )

    def test_none_store_returns_empty(self):
        mgr = SessionRecoveryManager(None)
        assert mgr.list_recoverable() == []
        status, cp = mgr.recover("sess-1")
        assert status == RecoveryStatus.NO_CHECKPOINT
