"""Tests for CLI streaming rendering and config."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from vibe.cli.main import app, interactive_mode
from vibe.core.query_loop import Metrics, QueryResult

runner = CliRunner()


def test_cli_stream_flag_passed_to_query_loop():
    """Verify that --stream option is passed correctly to QueryLoopFactory."""
    mock_loop = MagicMock()
    with patch("vibe.cli.main.QueryLoopFactory") as MockFactory, \
         patch("vibe.cli.main.interactive_mode", new_callable=AsyncMock), \
         patch("vibe.harness.memory.session_store.SessionStore") as MockStore:
        mock_store = MagicMock()
        mock_store.list_incomplete.return_value = []
        MockStore.return_value = mock_store

        MockFactory.return_value.create.return_value = mock_loop
        runner.invoke(app, ["main", "--stream"])

        # Verify QueryLoopFactory was instantiated with stream=True
        args, kwargs = MockFactory.call_args
        assert kwargs.get("stream") is True


def test_cli_interactive_streaming_output():
    """Verify that streaming chunks and metrics are rendered correctly in interactive mode."""
    mock_loop = MagicMock()
    mock_loop.config = MagicMock()
    mock_loop.config.llm.stream = True
    mock_loop.config.llm.show_reasoning = True

    async def mock_run(*args, **kwargs):
        # Yield status chunk
        yield QueryResult(
            is_status=True,
            status_message="Thinking...",
        )
        # Yield stream chunk 1
        yield QueryResult(
            response="chunk1",
            reasoning_content="thinking1",
            is_stream_chunk=True,
            is_chunk=True,
        )
        # Yield stream chunk 2
        yield QueryResult(
            response="chunk2",
            reasoning_content="thinking2",
            is_stream_chunk=True,
            is_chunk=True,
        )
        # Yield final non-stream result
        yield QueryResult(
            response="chunk1chunk2",
            reasoning_content="thinking1thinking2",
            is_stream_chunk=False,
            metrics=Metrics(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                elapsed_seconds=1.5,
                tokens_per_second=13.3,
                reasoning_tokens=5,
            )
        )

    mock_loop.run = mock_run

    # We want input() to submit a query and then exit
    inputs = ["hello", "/exit"]
    input_idx = 0
    def mock_input():
        nonlocal input_idx
        if input_idx < len(inputs):
            val = inputs[input_idx]
            input_idx += 1
            return val
        return "/exit"

    call_order = []
    
    spinner_mock = MagicMock()
    spinner_mock.stop.side_effect = lambda: call_order.append("stop")
    
    status_mock = MagicMock()
    status_mock.return_value = spinner_mock

    from vibe.cli.main import console
    original_print = console.print
    def spy_print(*args, **kwargs):
        if args and isinstance(args[0], str):
            call_order.append(f"print:{args[0]}")
        original_print(*args, **kwargs)

    with patch("builtins.input", side_effect=mock_input), \
         patch.object(console, "status", status_mock), \
         patch.object(console, "print", side_effect=spy_print), \
         console.capture() as capture:
        asyncio.run(interactive_mode(mock_loop))

    output = capture.get()

    # Verify that the chunks are printed
    assert "chunk1" in output
    assert "chunk2" in output

    # Since show_reasoning is True, reasoning content should be printed
    assert "thinking1" in output
    assert "thinking2" in output

    # Verify that metrics are printed
    assert "30 tokens" in output
    assert "5 reasoning" in output

    # Verify spinner assertions
    status_mock.assert_called_once_with("[dim]Thinking...[/dim]", spinner="dots")
    spinner_mock.start.assert_called_once()
    spinner_mock.update.assert_called_with("[dim]Thinking...[/dim]")
    spinner_mock.stop.assert_called_once()

    # Verify stop occurred before printing chunks
    assert "stop" in call_order
    stop_idx = call_order.index("stop")
    
    chunk1_prints = [i for i, x in enumerate(call_order) if "chunk1" in x]
    chunk2_prints = [i for i, x in enumerate(call_order) if "chunk2" in x]
    
    assert chunk1_prints, f"Expected print call containing chunk1, got call_order: {call_order}"
    assert chunk2_prints, f"Expected print call containing chunk2, got call_order: {call_order}"
    assert stop_idx < chunk1_prints[0]
    assert stop_idx < chunk2_prints[0]


def test_cli_single_query_streaming_output():
    """Verify that streaming chunks and metrics are rendered correctly in single query mode."""
    mock_loop = MagicMock()
    mock_loop.config = MagicMock()
    mock_loop.config.llm.stream = True
    mock_loop.config.llm.show_reasoning = True

    async def mock_run(*args, **kwargs):
        # Yield status chunk
        yield QueryResult(
            is_status=True,
            status_message="Thinking...",
        )
        # Yield stream chunk 1
        yield QueryResult(
            response="chunk1",
            reasoning_content="thinking1",
            is_stream_chunk=True,
            is_chunk=True,
        )
        # Yield stream chunk 2
        yield QueryResult(
            response="chunk2",
            reasoning_content="thinking2",
            is_stream_chunk=True,
            is_chunk=True,
        )
        # Yield final non-stream result
        yield QueryResult(
            response="chunk1chunk2",
            reasoning_content="thinking1thinking2",
            is_stream_chunk=False,
            metrics=Metrics(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                elapsed_seconds=1.5,
                tokens_per_second=13.3,
                reasoning_tokens=5,
            )
        )

    mock_loop.run = mock_run

    call_order = []
    
    spinner_mock = MagicMock()
    spinner_mock.stop.side_effect = lambda: call_order.append("stop")
    
    status_mock = MagicMock()
    status_mock.return_value = spinner_mock

    from vibe.cli.main import console, single_query_mode
    original_print = console.print
    def spy_print(*args, **kwargs):
        if args and isinstance(args[0], str):
            call_order.append(f"print:{args[0]}")
        original_print(*args, **kwargs)

    with patch.object(console, "status", status_mock), \
         patch.object(console, "print", side_effect=spy_print), \
         console.capture() as capture:
        asyncio.run(single_query_mode(mock_loop, "hello"))

    output = capture.get()

    # Verify that the chunks are printed
    assert "chunk1" in output
    assert "chunk2" in output

    # Since show_reasoning is True, reasoning content should be printed
    assert "thinking1" in output
    assert "thinking2" in output

    # Verify that metrics are printed
    assert "30 tokens" in output
    assert "5 reasoning" in output

    # Verify spinner assertions
    status_mock.assert_called_once_with("[dim]Thinking...[/dim]", spinner="dots")
    spinner_mock.start.assert_called_once()
    spinner_mock.update.assert_called_with("[dim]Thinking...[/dim]")
    spinner_mock.stop.assert_called_once()

    # Verify stop occurred before printing chunks
    assert "stop" in call_order
    stop_idx = call_order.index("stop")
    
    chunk1_prints = [i for i, x in enumerate(call_order) if "chunk1" in x]
    chunk2_prints = [i for i, x in enumerate(call_order) if "chunk2" in x]
    
    assert chunk1_prints, f"Expected print call containing chunk1, got call_order: {call_order}"
    assert chunk2_prints, f"Expected print call containing chunk2, got call_order: {call_order}"
    assert stop_idx < chunk1_prints[0]
    assert stop_idx < chunk2_prints[0]
