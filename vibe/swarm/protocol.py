"""AgentProtocol — Pub/Sub message bus for multi-agent communication.

EventBroker decouples producers from consumers. Agents subscribe to topics
or MessageTypes. Dead Letter Queue captures failed messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any


class MessageType(Enum):
    TASK = auto()  # Orchestrator assigns task to agent
    RESULT = auto()  # Agent reports task completion
    QUESTION = auto()  # Agent asks for clarification
    ANSWER = auto()  # Response to question
    CRITIQUE = auto()  # Critic agent feedback
    UPDATE_WIKI = auto()  # Request wiki update (orchestrator-owned)
    BROADCAST = auto()  # Global announcement
    DONE = auto()  # Agent signals completion
    ERROR = auto()  # Agent reports failure
    HEARTBEAT = auto()  # Keep-alive ping


@dataclass(frozen=True)
class AgentMessage:
    """Immutable message passed between agents via the message bus."""

    msg_type: MessageType
    sender: str  # Agent ID or "orchestrator"
    recipient: str | None  # None = broadcast
    content: str
    correlation_id: str  # Links related messages
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def reply(self, content: str, msg_type: MessageType = MessageType.ANSWER) -> AgentMessage:
        """Create a reply message with the same correlation_id."""
        return AgentMessage(
            msg_type=msg_type,
            sender="orchestrator",  # Will be overridden by actual sender
            recipient=self.sender,
            content=content,
            correlation_id=self.correlation_id,
        )


@dataclass
class DeadLetterEntry:
    """Failed message with error context for debugging."""

    message: AgentMessage
    error: str
    failed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    retry_count: int = 0


class EventBroker:
    """Pub/Sub message broker with topic-based routing and dead letter queue.

    Agents subscribe to topics (MessageType values or "all"). Messages are
    routed to all subscribers matching the topic. No point-to-point coupling.
    """

    def __init__(self, max_dlq_size: int = 1000):
        self._subscribers: dict[str, list[asyncio.Queue[AgentMessage]]] = {}
        self._dlq: asyncio.Queue[DeadLetterEntry] = asyncio.Queue(maxsize=max_dlq_size)
        self._lock = asyncio.Lock()
        self._running = True

    async def subscribe(self, topic: str) -> asyncio.Queue[AgentMessage]:
        """Subscribe to a topic. Returns a queue that receives matching messages."""
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(topic, []).append(queue)
        return queue

    async def unsubscribe(self, topic: str, queue: asyncio.Queue[AgentMessage]) -> None:
        """Remove a subscription."""
        async with self._lock:
            if topic in self._subscribers:
                self._subscribers[topic] = [q for q in self._subscribers[topic] if q is not queue]

    async def publish(self, message: AgentMessage, topics: list[str] | None = None) -> int:
        """Publish a message to all matching subscribers.

        Returns the number of subscribers that received the message.
        """
        if not self._running:
            return 0

        # Default topics: message type + "all"
        if topics is None:
            topics = [message.msg_type.name, "all"]
            if message.recipient:
                topics.append(f"agent:{message.recipient}")

        delivered = 0
        delivered_queues: set[asyncio.Queue[AgentMessage]] = set()
        async with self._lock:
            for topic in topics:
                for queue in self._subscribers.get(topic, []):
                    if queue in delivered_queues:
                        continue  # Skip duplicates
                    try:
                        queue.put_nowait(message)
                        delivered_queues.add(queue)
                        delivered += 1
                    except asyncio.QueueFull:
                        await self._dlq.put(
                            DeadLetterEntry(
                                message=message,
                                error=f"Queue full for topic {topic}",
                            )
                        )
        return delivered

    async def get_dlq(self) -> list[DeadLetterEntry]:
        """Return all dead letter entries (non-destructive)."""
        entries: list[DeadLetterEntry] = []
        # Queue doesn't support iteration, so we drain and re-fill
        temp: list[DeadLetterEntry] = []
        while not self._dlq.empty():
            try:
                temp.append(self._dlq.get_nowait())
            except asyncio.QueueEmpty:
                break
        for entry in temp:
            entries.append(entry)
            await self._dlq.put(entry)
        return entries

    async def shutdown(self) -> None:
        """Gracefully shut down the broker."""
        self._running = False
        async with self._lock:
            for queues in self._subscribers.values():
                for queue in queues:
                    queue.put_nowait(
                        AgentMessage(
                            msg_type=MessageType.DONE,
                            sender="broker",
                            recipient=None,
                            content="shutdown",
                            correlation_id="system",
                        )
                    )


class MessageBus:
    """High-level message bus wrapping EventBroker for agent convenience."""

    def __init__(self, broker: EventBroker | None = None):
        self.broker = broker or EventBroker()
        self._agent_queues: dict[str, asyncio.Queue[AgentMessage]] = {}

    async def register_agent(self, agent_id: str) -> asyncio.Queue[AgentMessage]:
        """Register an agent and return its personal message queue."""
        queue = await self.broker.subscribe(f"agent:{agent_id}")
        self._agent_queues[agent_id] = queue
        return queue

    async def send(
        self,
        msg_type: MessageType,
        sender: str,
        recipient: str | None,
        content: str,
        correlation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Send a message. Returns delivery count."""
        message = AgentMessage(
            msg_type=msg_type,
            sender=sender,
            recipient=recipient,
            content=content,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )
        return await self.broker.publish(message)

    async def broadcast(
        self,
        sender: str,
        content: str,
        correlation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Broadcast to all agents."""
        message = AgentMessage(
            msg_type=MessageType.BROADCAST,
            sender=sender,
            recipient=None,
            content=content,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )
        # Publish to "all" topic plus each agent's personal topic
        topics = ["all"]
        for agent_id in self._agent_queues:
            topics.append(f"agent:{agent_id}")
        return await self.broker.publish(message, topics=topics)

    async def shutdown(self) -> None:
        await self.broker.shutdown()
