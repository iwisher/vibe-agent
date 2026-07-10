"""Tests for the SubAgentRunner."""

import asyncio
from unittest.mock import MagicMock

import pytest

from vibe.core.query_loop import QueryResult
from vibe.core.sub_agent import SubAgentRunner


class TestSubAgentRunner:
    @pytest.mark.asyncio
    async def test_successful_run(self):
        # Create a mock QueryLoop
        mock_loop = MagicMock()
        mock_loop.add_user_message = MagicMock()

        # Define an async generator to mock loop.run()
        async def mock_run():
            yield QueryResult(is_status=True, status_message="Thinking...")
            yield QueryResult(is_stream_chunk=True, response="Hello ")
            yield QueryResult(is_stream_chunk=True, response="world")
            yield QueryResult(response="Hello world")

        mock_loop.run = mock_run

        # Create mock Factory
        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        runner = SubAgentRunner(mock_factory, "test-session")
        await runner.start("Say hello")

        # Wait for completion
        results = await runner.wait(timeout=1.0)
        assert len(results) == 4
        assert runner.is_done()

        # Extract final response
        final_resp = runner.extract_final_response()
        assert final_resp == "Hello world"

    @pytest.mark.asyncio
    async def test_error_handling(self):
        mock_loop = MagicMock()
        mock_loop._state = "ERROR"

        # Define an async generator that raises an exception
        async def mock_run_error():
            yield QueryResult(is_status=True, status_message="Thinking...")
            raise ValueError("LLM Error")

        mock_loop.run = mock_run_error

        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        runner = SubAgentRunner(mock_factory, "test-session")
        await runner.start("Say hello")

        results = await runner.wait(timeout=1.0)
        # Results should contain the status message and the error QueryResult appended by
        # the except block
        assert len(results) == 2
        assert results[1].error is not None
        assert isinstance(results[1].error, ValueError)
        assert str(results[1].error) == "LLM Error"
        assert runner.is_done()

    @pytest.mark.asyncio
    async def test_stop_cancellation(self):
        mock_loop = MagicMock()

        async def mock_slow_run():
            yield QueryResult(is_status=True, status_message="Thinking...")
            await asyncio.sleep(2.0)
            yield QueryResult(response="Done")

        mock_loop.run = mock_slow_run

        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_loop

        runner = SubAgentRunner(mock_factory, "test-session")
        await runner.start("Slow query")

        await asyncio.sleep(0.05)
        assert not runner.is_done()

        # Stop / Cancel runner
        runner.stop()
        await asyncio.sleep(0.05)

        assert runner.is_done()
        # Verify loop stop was called
        mock_loop.stop.assert_called_once()
