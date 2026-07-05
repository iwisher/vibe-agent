import asyncio
from unittest.mock import MagicMock

import pytest

from vibe.core.query_loop import Metrics, QueryResult, QueryState
from vibe.core.session_controller import OutputEvent, SessionController
from vibe.tools.tool_system import ToolResult


@pytest.mark.asyncio
async def test_session_controller_queues_messages_sequentially():
    mock_loop = MagicMock()
    mock_loop.state = QueryState.IDLE
    mock_loop.add_user_message = MagicMock()

    run_calls = 0

    async def mock_run():
        nonlocal run_calls
        run_calls += 1
        yield QueryResult(response=f"Response {run_calls}")

    mock_loop.run = mock_run

    mock_factory = MagicMock()
    mock_factory.create.return_value = mock_loop

    controller = SessionController(mock_factory)
    await controller.start()

    # Queue multiple messages with /queue to prevent interruption
    await controller.queue.enqueue("/queue Message 1")
    await controller.queue.enqueue("/queue Message 2")

    # Retrieve first response
    event1 = await asyncio.wait_for(controller.output_queue.get(), timeout=1.0)
    assert event1.result.response == "Response 1"
    mock_loop.add_user_message.assert_any_call("Message 1")

    # Retrieve second response
    event2 = await asyncio.wait_for(controller.output_queue.get(), timeout=1.0)
    assert event2.result.response == "Response 2"
    mock_loop.add_user_message.assert_any_call("Message 2")

    await controller.shutdown()


@pytest.mark.asyncio
async def test_session_controller_normal_message_interrupts():
    mock_loop = MagicMock()
    mock_loop.state = QueryState.IDLE
    mock_loop.add_user_message = MagicMock()
    mock_loop.stop = MagicMock()

    async def mock_run():
        # Yield first iteration completion (no stream chunk, no status)
        yield QueryResult(response="First iteration")
        # Yield second iteration completion
        yield QueryResult(response="Second iteration")

    mock_loop.run = mock_run

    mock_factory = MagicMock()
    mock_factory.create.return_value = mock_loop

    controller = SessionController(mock_factory)
    await controller.start()

    # Start initial run
    await controller.queue.enqueue("First input")

    # Queue an immediate message (normal input without /queue)
    await controller.queue.enqueue("Immediate message")

    # Check output queue
    event = await asyncio.wait_for(controller.output_queue.get(), timeout=1.0)
    assert event.result.response == "First iteration"

    # Since next message in queue is normal, main_loop.stop() should have been called
    assert mock_loop.stop.called is True

    await controller.shutdown()


@pytest.mark.asyncio
async def test_session_controller_prompt_shown_transitions():
    mock_factory = MagicMock()
    controller = SessionController(mock_factory)
    assert controller.prompt_shown is True

    from vibe.cli.main import _output_consumer

    result = QueryResult(
        response="Test response",
        metrics=Metrics(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            elapsed_seconds=1.0,
            tokens_per_second=30.0,
        ),
    )

    await controller.output_queue.put(OutputEvent("main", result))
    task = asyncio.create_task(_output_consumer(controller))

    await asyncio.sleep(0.05)
    task.cancel()

    # The consumer should have cleared prompt_shown to print,
    # then reset it back to True since queue is empty
    assert controller.prompt_shown is True


@pytest.mark.asyncio
async def test_output_consumer_formats_rules_and_metrics():
    mock_factory = MagicMock()
    controller = SessionController(mock_factory)

    from vibe.cli.main import _output_consumer

    # Put an event in the output queue
    tr = ToolResult(success=True, content="Success content")
    metrics = Metrics(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        elapsed_seconds=1.0,
        tokens_per_second=30.0,
    )

    result = QueryResult(
        response="Final response", tool_results=[tr], metrics=metrics, is_stream_chunk=False
    )
    await controller.output_queue.put(OutputEvent("main", result))

    # Consume output using capture
    from vibe.cli.main import console

    task = asyncio.create_task(_output_consumer(controller))

    with console.capture() as capture:
        await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    output = capture.get()
    # Verify rule divider, robot label, tool result, and metrics are rendered
    assert "🤖 Vibe Agent" in output
    assert "Tool Result" in output
    assert "Success content" in output
    assert "30 tokens" in output
