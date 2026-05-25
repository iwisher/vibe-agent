"""Tests for the SessionController."""

import asyncio
from unittest.mock import MagicMock, patch
import pytest

from vibe.core.conversation_queue import SteerCommand
from vibe.core.query_loop import Message, QueryResult, QueryState
from vibe.core.session_controller import OutputEvent, SessionController


class TestSessionController:
    @pytest.mark.asyncio
    async def test_main_worker_orchestration(self):
        # Create a mock QueryLoop
        mock_loop = MagicMock()
        mock_loop.state = QueryState.IDLE
        mock_loop.add_user_message = MagicMock()

        async def mock_run():
            yield QueryResult(response="Response to message")

        mock_loop.run = mock_run

        # Create mock Factory
        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        controller = SessionController(mock_factory)
        await controller.start()

        # Enqueue a message
        await controller.queue.enqueue("Hello main")

        # Wait for the output to stream to output_queue
        event = await asyncio.wait_for(controller.output_queue.get(), timeout=1.0)
        assert isinstance(event, OutputEvent)
        assert event.source == "main"
        assert event.result.response == "Response to message"

        mock_loop.add_user_message.assert_called_once_with("Hello main")

        await controller.shutdown()

    @pytest.mark.asyncio
    async def test_handle_steer(self):
        mock_loop = MagicMock()
        mock_loop.messages = []
        mock_loop.stop = MagicMock()
        mock_loop.set_model = MagicMock()

        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        controller = SessionController(mock_factory)
        await controller.start()

        # Send inject_context steer
        await controller.queue.steer(SteerCommand(type="inject_context", payload="System prompt override"))
        # Send switch_model steer
        await controller.queue.steer(SteerCommand(type="switch_model", payload="gpt-4o"))
        # Send stop steer
        await controller.queue.steer(SteerCommand(type="stop"))

        # Wait a short moment for the worker to handle the steer commands
        await asyncio.sleep(0.05)

        # Check steer commands were processed
        assert len(mock_loop.messages) == 1
        assert mock_loop.messages[0].content == "System prompt override"
        mock_loop.set_model.assert_called_once_with("gpt-4o")
        mock_loop.stop.assert_called_once()

        await controller.shutdown()

    @pytest.mark.asyncio
    async def test_send_bg_agent(self):
        # We will mock SubAgentRunner to control when it finishes and its results
        mock_loop = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        controller = SessionController(mock_factory)

        # Patch SubAgentRunner
        with patch("vibe.core.session_controller.SubAgentRunner") as mock_runner_class:
            mock_runner = MagicMock()
            mock_runner.results = [
                QueryResult(is_status=True, status_message="Thinking..."),
                QueryResult(response="Live chunk"),
            ]
            mock_runner.is_done.side_effect = [False, True]
            
            async def mock_start(q):
                pass
            mock_runner.start = mock_start
            
            mock_runner_class.return_value = mock_runner

            agent_id = await controller.send_bg("Perform background task")

            assert agent_id == "bg_0"
            assert controller.bg_agents["bg_0"] == mock_runner

            # Verify bg output was streamed to the controller's output_queue
            event1 = await asyncio.wait_for(controller.output_queue.get(), timeout=1.0)
            assert event1.source == "bg_0"
            assert event1.result.is_status is True

            event2 = await asyncio.wait_for(controller.output_queue.get(), timeout=1.0)
            assert event2.source == "bg_0"
            assert event2.result.response == "Live chunk"

        await controller.shutdown()

    @pytest.mark.asyncio
    async def test_send_btw_agent(self):
        mock_loop = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        controller = SessionController(mock_factory)

        with patch("vibe.core.session_controller.SubAgentRunner") as mock_runner_class:
            mock_runner = MagicMock()
            
            async def mock_start(q):
                pass
            mock_runner.start = mock_start

            async def mock_wait(timeout=None):
                return mock_runner.results

            mock_runner.wait = mock_wait
            mock_runner.extract_final_response.return_value = "This is a side note."
            mock_runner_class.return_value = mock_runner

            agent_id = await controller.send_btw("Check this side item")
            assert agent_id == "btw"
            assert controller.btw_agent == mock_runner

            # The completion monitor should await the runner and enqueue the result
            item = await asyncio.wait_for(controller.queue.next_item(), timeout=1.0)
            assert item.source == "btw_result"
            assert item.content == "[btw result] This is a side note."

        await controller.shutdown()
