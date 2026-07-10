"""Tests for the thread-safe ConversationQueue."""

import asyncio

import pytest

from vibe.core.conversation_queue import ConversationQueue, QueuedMessage, SteerCommand


class TestConversationQueue:
    @pytest.mark.asyncio
    async def test_fifo_ordering(self):
        queue = ConversationQueue()
        await queue.enqueue("Message 1")
        await queue.enqueue("Message 2")

        assert queue.pending_count == 2
        assert queue.steer_count == 0

        item1 = await queue.next_item()
        assert isinstance(item1, QueuedMessage)
        assert item1.content == "Message 1"
        assert item1.source == "user"

        item2 = await queue.next_item()
        assert isinstance(item2, QueuedMessage)
        assert item2.content == "Message 2"

        assert queue.pending_count == 0

    @pytest.mark.asyncio
    async def test_steer_priority(self):
        queue = ConversationQueue()
        await queue.enqueue("Message 1")
        await queue.enqueue("Message 2")

        # Inject steer command
        cmd = SteerCommand(type="stop")
        await queue.steer(cmd)

        assert queue.pending_count == 2
        assert queue.steer_count == 1

        # The first item returned must be the steer command, even though it was added last
        item1 = await queue.next_item()
        assert isinstance(item1, SteerCommand)
        assert item1.type == "stop"

        item2 = await queue.next_item()
        assert isinstance(item2, QueuedMessage)
        assert item2.content == "Message 1"

    @pytest.mark.asyncio
    async def test_peek(self):
        queue = ConversationQueue()
        assert queue.peek() is None

        await queue.enqueue("Message 1")
        # Injects steer command
        cmd = SteerCommand(type="stop")
        await queue.steer(cmd)

        # peek() should ignore steer commands and return next pending message
        peeked = queue.peek()
        assert isinstance(peeked, QueuedMessage)
        assert peeked.content == "Message 1"

    @pytest.mark.asyncio
    async def test_blocking_wait(self):
        queue = ConversationQueue()

        async def enqueue_later():
            await asyncio.sleep(0.05)
            await queue.enqueue("Message 1")

        asyncio.create_task(enqueue_later())

        # This should block until enqueue_later runs
        item = await queue.next_item()
        assert isinstance(item, QueuedMessage)
        assert item.content == "Message 1"
