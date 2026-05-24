"""Durable session suspension and resumption for crash recovery.

Provides automatic checkpointing on state transitions and
resumption from checkpoints after process crashes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class RecoveryStatus(Enum):
    """Status of a session recovery attempt."""

    NO_CHECKPOINT = auto()
    RESTORED = auto()
    CORRUPTED = auto()
    EXPIRED = auto()


@dataclass
class SessionCheckpoint:
    """Full checkpoint of a QueryLoop session."""

    session_id: str
    state: str
    messages: list[dict[str, Any]]
    plan_result: dict[str, Any] | None
    iteration: int
    feedback_retries: int
    model: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "messages": self.messages,
            "plan_result": self.plan_result,
            "iteration": self.iteration,
            "feedback_retries": self.feedback_retries,
            "model": self.model,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionCheckpoint":
        return cls(
            session_id=data["session_id"],
            state=data["state"],
            messages=data.get("messages", []),
            plan_result=data.get("plan_result"),
            iteration=data.get("iteration", 0),
            feedback_retries=data.get("feedback_retries", 0),
            model=data.get("model"),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
        )


class SessionRecoveryManager:
    """Manages durable session checkpointing and recovery.

    Works with SessionStore to provide:
    1. Automatic checkpointing on state transitions
    2. Recovery from checkpoints after crashes
    3. Checkpoint expiration and cleanup
    """

    DEFAULT_CHECKPOINT_TTL_SECONDS = 86400  # 24 hours

    def __init__(
        self,
        session_store: Any,
        checkpoint_ttl_seconds: float = DEFAULT_CHECKPOINT_TTL_SECONDS,
    ):
        self.session_store = session_store
        self.checkpoint_ttl = checkpoint_ttl_seconds

    def save_checkpoint(
        self,
        session_id: str,
        state: str,
        messages: list[dict[str, Any]],
        plan_result: dict[str, Any] | None = None,
        iteration: int = 0,
        feedback_retries: int = 0,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save a checkpoint to the session store."""
        if self.session_store is None:
            return

        try:
            self.session_store.save_checkpoint(
                session_id=session_id,
                state=state,
                messages=messages,
                plan_result=plan_result,
                iteration=iteration,
                feedback_retries=feedback_retries,
                model=model,
            )
        except Exception:
            pass  # Checkpoint failures must not crash the session

    def recover(self, session_id: str) -> tuple[RecoveryStatus, SessionCheckpoint | None]:
        """Attempt to recover a session from its checkpoint.

        Returns (status, checkpoint) tuple.
        """
        if self.session_store is None:
            return RecoveryStatus.NO_CHECKPOINT, None

        try:
            data = self.session_store.load_checkpoint(session_id)
        except Exception:
            return RecoveryStatus.CORRUPTED, None

        if data is None:
            return RecoveryStatus.NO_CHECKPOINT, None

        try:
            checkpoint = SessionCheckpoint(
                session_id=data["session_id"],
                state=data["state"],
                messages=json.loads(data["messages"]) if isinstance(data["messages"], str) else data["messages"],
                plan_result=json.loads(data["plan_result"]) if isinstance(data.get("plan_result"), str) else data.get("plan_result"),
                iteration=data.get("iteration", 0),
                feedback_retries=data.get("feedback_retries", 0),
                model=data.get("model"),
            )
        except (KeyError, json.JSONDecodeError):
            return RecoveryStatus.CORRUPTED, None

        # Check expiration using the data dict's created_at if available
        created_at = data.get("created_at", checkpoint.created_at)
        if isinstance(created_at, str):
            try:
                from datetime import datetime
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            except Exception:
                created_at = checkpoint.created_at
        age = time.time() - created_at
        if age > self.checkpoint_ttl:
            return RecoveryStatus.EXPIRED, checkpoint

        return RecoveryStatus.RESTORED, checkpoint

    def list_recoverable(self) -> list[str]:
        """List session IDs that have valid checkpoints."""
        if self.session_store is None:
            return []

        try:
            checkpoints = self.session_store.list_incomplete(limit=100)
            recoverable = []
            for cp in checkpoints:
                session_id = cp.get("session_id")
                if not session_id:
                    continue
                status, _ = self.recover(session_id)
                if status == RecoveryStatus.RESTORED:
                    recoverable.append(session_id)
            return recoverable
        except Exception:
            return []

    def delete_checkpoint(self, session_id: str) -> bool:
        """Delete a checkpoint."""
        if self.session_store is None:
            return False
        try:
            return self.session_store.delete_checkpoint(session_id)
        except Exception:
            return False
