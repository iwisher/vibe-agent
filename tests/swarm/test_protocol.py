"""Tests for AgentProtocol message bus and EventBroker."""

import pytest
import asyncio

from vibe.swarm.protocol import (
    AgentMessage,
    MessageType,
    EventBroker,
    MessageBus,
    DeadLetterEntry,
)


class TestAgentMessage:
    def test_message_creation(self):
        msg = AgentMessage(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            recipient="agent-1",
            content="Do something",
            correlation_id="task-123",
        )
        assert msg.msg_type == MessageType.TASK
        assert msg.sender == "orchestrator"
        assert msg.recipient == "agent-1"
        assert msg.content == "Do something"
        assert msg.correlation_id == "task-123"
        assert msg.timestamp is not None

    def test_message_reply(self):
        msg = AgentMessage(
            msg_type=MessageType.QUESTION,
            sender="agent-1",
            recipient="orchestrator",
            content="What is the API key?",
            correlation_id="task-123",
        )
        reply = msg.reply("Here is the key", MessageType.ANSWER)
        assert reply.msg_type == MessageType.ANSWER
        assert reply.recipient == "agent-1"
        assert reply.correlation_id == "task-123"
        assert reply.content == "Here is the key"


class TestEventBroker:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        broker = EventBroker()
        queue = await broker.subscribe("TASK")

        msg = AgentMessage(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            recipient="agent-1",
            content="Test task",
            correlation_id="corr-1",
        )
        delivered = await broker.publish(msg)
        assert delivered >= 1

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.content == "Test task"
        assert received.correlation_id == "corr-1"

    @pytest.mark.asyncio
    async def test_broadcast_to_all_subscribers(self):
        broker = EventBroker()
        q1 = await broker.subscribe("all")
        q2 = await broker.subscribe("all")

        msg = AgentMessage(
            msg_type=MessageType.BROADCAST,
            sender="orchestrator",
            recipient=None,
            content="Hello all",
            correlation_id="bcast-1",
        )
        delivered = await broker.publish(msg, topics=["all"])
        assert delivered == 2

        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.content == "Hello all"
        assert r2.content == "Hello all"

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        broker = EventBroker()
        queue = await broker.subscribe("TASK")
        await broker.unsubscribe("TASK", queue)

        msg = AgentMessage(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            recipient="agent-1",
            content="Test",
            correlation_id="corr-1",
        )
        delivered = await broker.publish(msg, topics=["TASK"])
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_shutdown(self):
        broker = EventBroker()
        queue = await broker.subscribe("all")
        await broker.shutdown()

        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg.msg_type == MessageType.DONE

    @pytest.mark.asyncio
    async def test_publish_after_shutdown(self):
        broker = EventBroker()
        await broker.shutdown()

        msg = AgentMessage(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            recipient="agent-1",
            content="Test",
            correlation_id="corr-1",
        )
        delivered = await broker.publish(msg)
        assert delivered == 0


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_register_agent(self):
        bus = MessageBus()
        queue = await bus.register_agent("agent-1")
        assert queue is not None
        assert "agent-1" in bus._agent_queues

    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        bus = MessageBus()
        queue = await bus.register_agent("agent-1")

        delivered = await bus.send(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            recipient="agent-1",
            content="Do work",
            correlation_id="task-1",
        )
        assert delivered >= 1

        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg.content == "Do work"
        assert msg.msg_type == MessageType.TASK

    @pytest.mark.asyncio
    async def test_broadcast(self):
        bus = MessageBus()
        q1 = await bus.register_agent("agent-1")
        q2 = await bus.register_agent("agent-2")

        delivered = await bus.broadcast(
            sender="orchestrator",
            content="System update",
            correlation_id="bcast-1",
        )
        assert delivered >= 2

        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.content == "System update"
        assert r2.content == "System update"

    @pytest.mark.asyncio
    async def test_shutdown(self):
        bus = MessageBus()
        queue = await bus.register_agent("agent-1")
        await bus.shutdown()

        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg.msg_type == MessageType.DONE
