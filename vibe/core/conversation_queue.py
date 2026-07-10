"""Thread-safe message queue with steer command support for a single conversation."""

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class QueuedMessage:
    content: str
    timestamp: float
    source: str = "user"  # "user" | "system" | "btw_result"


@dataclass
class SteerCommand:
    type: Literal["stop", "inject_context", "switch_model"]
    payload: Any = None


class ConversationQueue:
    """Thread-safe FIFO queue for conversation messages with priority steer commands."""

    def __init__(self) -> None:
        self._pending: deque[QueuedMessage] = deque()
        self._steer: deque[SteerCommand] = deque()
        self._lock = asyncio.Lock()
        self._message_event = asyncio.Event()

    async def enqueue(self, content: str, source: str = "user") -> None:
        """Queue a normal message."""
        async with self._lock:
            self._pending.append(
                QueuedMessage(
                    content=content, timestamp=asyncio.get_event_loop().time(), source=source
                )
            )
            self._message_event.set()

    async def steer(self, cmd: SteerCommand) -> None:
        """Inject a steer command (processed before pending messages)."""
        async with self._lock:
            self._steer.append(cmd)
            self._message_event.set()

    async def next_item(self) -> QueuedMessage | SteerCommand | None:
        """Get next item: steer commands have priority over queued messages.

        Returns None only if queue is empty and no steer commands pending.
        Blocks until an item is available.
        """
        while True:
            async with self._lock:
                if self._steer:
                    return self._steer.popleft()
                if self._pending:
                    return self._pending.popleft()
                self._message_event.clear()

            await self._message_event.wait()

    def peek(self) -> QueuedMessage | None:
        """Non-blocking peek at next pending message (excludes steer commands)."""
        for item in self._pending:
            return item
        return None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def steer_count(self) -> int:
        return len(self._steer)
