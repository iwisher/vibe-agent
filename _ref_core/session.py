"""Session management for conversation state."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .query_loop import Message


@dataclass
class SessionConfig:
    """Configuration for a session."""
    max_iterations: int = 50
    max_context_tokens: int = 100000
    enable_compaction: bool = True
    enable_error_recovery: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class Session:
    """A conversation session with state management."""

    def __init__(self, config: Optional[SessionConfig] = None):
        self.id = str(uuid.uuid4())
        self.config = config or SessionConfig()
        self.messages: List[Message] = []
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()
        self.metadata: Dict[str, Any] = {}
        self._iteration_count = 0

    def add_message(self, message: Message) -> None:
        """Add a message to the session."""
        self.messages.append(message)
        self.last_activity = datetime.utcnow()

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self.add_message(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message."""
        self.add_message(Message(role="assistant", content=content))

    def clear_messages(self) -> None:
        """Clear all messages."""
        self.messages.clear()
        self._iteration_count = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary."""
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "message_count": len(self.messages),
            "config": {
                "max_iterations": self.config.max_iterations,
                "max_context_tokens": self.config.max_context_tokens,
            },
            "metadata": self.metadata,
        }

    @property
    def iteration_count(self) -> int:
        return self._iteration_count

    def increment_iteration(self) -> None:
        self._iteration_count += 1

    def is_expired(self, max_age_hours: int = 24) -> bool:
        """Check if session has expired."""
        from datetime import timedelta
        age = datetime.utcnow() - self.last_activity
        return age > timedelta(hours=max_age_hours)
