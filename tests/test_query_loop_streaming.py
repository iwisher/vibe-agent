import asyncio
from unittest.mock import AsyncMock

import pytest

from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.core.query_loop import QueryLoop, QueryState
from vibe.tools.tool_system import Tool, ToolResult, ToolSystem


class DummyTool(Tool):
    async def execute(self, **kwargs):
        return ToolResult(success=True, content="done")

    def get_schema(self):
        return {"type": "object"}


@pytest.fixture
def mock_llm():
    m = AsyncMock(spec=LLMClient)
    m.model = "test-model"
    return m


@pytest.fixture
def tool_system():
    ts = ToolSystem()
    ts.register_tool(DummyTool("dummy", "dummy"))
    return ts


@pytest.mark.asyncio
async def test_query_loop_stream_yields_chunks(mock_llm, tool_system):
    async def mock_stream(*args, **kwargs):
        yield LLMResponse(content="Hello", finish_reason=None)
        yield LLMResponse(content=" World", finish_reason="stop")

    mock_llm.complete_stream.side_effect = mock_stream
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = [r async for r in loop.run("hi", stream=True)]

    # Filter out status messages and final completions to check intermediate chunks
    chunks = [r for r in results if not r.is_status and r.is_stream_chunk]
    assert len(chunks) == 2
    assert chunks[0].response == "Hello"
    assert chunks[1].response == " World"
    assert chunks[0].is_stream_chunk is True
    assert chunks[1].is_stream_chunk is True


@pytest.mark.asyncio
async def test_query_loop_stream_aggregates_content(mock_llm, tool_system):
    async def mock_stream(*args, **kwargs):
        yield LLMResponse(content="Part 1, ", finish_reason=None)
        yield LLMResponse(content="Part 2", finish_reason="stop")

    mock_llm.complete_stream.side_effect = mock_stream
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = [r async for r in loop.run("hi", stream=True)]

    # The final completed result is not status and is not a chunk
    final_results = [r for r in results if not r.is_status and not r.is_stream_chunk]
    assert len(final_results) == 1
    assert final_results[0].response == "Part 1, Part 2"
    assert final_results[0].state == QueryState.COMPLETED


@pytest.mark.asyncio
async def test_query_loop_stream_aggregates_reasoning(mock_llm, tool_system):
    async def mock_stream(*args, **kwargs):
        yield LLMResponse(content="Hello", reasoning_content="Thinking", finish_reason=None)
        yield LLMResponse(content=" World", reasoning_content=" more...", finish_reason="stop")

    mock_llm.complete_stream.side_effect = mock_stream
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = [r async for r in loop.run("hi", stream=True)]

    chunks = [r for r in results if not r.is_status and r.is_stream_chunk]
    assert len(chunks) == 2
    assert chunks[0].reasoning_content == "Thinking"
    assert chunks[1].reasoning_content == " more..."

    final_results = [r for r in results if not r.is_status and not r.is_stream_chunk]
    assert len(final_results) == 1
    assert final_results[0].response == "Hello World"
    assert final_results[0].reasoning_content == "Thinking more..."


@pytest.mark.asyncio
async def test_query_loop_stream_no_regression_blocking(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(
        content="blocking content", reasoning_content="blocking logic"
    )
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = [r async for r in loop.run("hi", stream=False)]

    # Intermediate chunks must be empty since stream=False
    chunks = [r for r in results if not r.is_status and r.is_stream_chunk]
    assert len(chunks) == 0

    final_results = [r for r in results if not r.is_status]
    assert len(final_results) == 1
    assert final_results[0].response == "blocking content"
    assert final_results[0].reasoning_content == "blocking logic"
    assert final_results[0].state == QueryState.COMPLETED

    mock_llm.complete.assert_called_once()
    mock_llm.complete_stream.assert_not_called()


@pytest.mark.asyncio
async def test_query_loop_stream_tool_calls_wait(mock_llm, tool_system):
    tool_executed = False

    class TrackingTool(Tool):
        async def execute(self, **kwargs):
            nonlocal tool_executed
            tool_executed = True
            return ToolResult(success=True, content="tool done")

        def get_schema(self):
            return {"type": "object"}

    ts = ToolSystem()
    ts.register_tool(TrackingTool("tracking", "tracking"))

    stream_finished = False
    call_count = 0

    async def mock_stream(*args, **kwargs):
        nonlocal call_count, stream_finished
        if call_count == 0:
            call_count += 1
            # Yield first chunk representing part of tool call
            yield LLMResponse(
                content="",
                tool_calls=[
                    {
                        "index": 0,
                        "id": "tc-1",
                        "function": {"name": "tracking", "arguments": ""},
                    }
                ],
                finish_reason=None,
            )
            await asyncio.sleep(0.05)

            # Tool must NOT have executed yet
            assert not tool_executed

            # Yield second chunk completing the tool call
            yield LLMResponse(
                content="",
                tool_calls=[{"index": 0, "function": {"arguments": "{}"}}],
                finish_reason="stop",
            )
            stream_finished = True
        else:
            yield LLMResponse(content="final answer", finish_reason="stop")

    mock_llm.complete_stream.side_effect = mock_stream

    loop = QueryLoop(llm_client=mock_llm, tool_system=ts)

    results = []
    async for r in loop.run("hi", stream=True):
        if not r.is_status:
            results.append(r)

    assert stream_finished
    assert tool_executed

    # We should have the synthesized response at the end
    assert results[-1].response == "final answer"


@pytest.mark.asyncio
async def test_query_loop_stream_default_is_true(mock_llm, tool_system):
    # Verify that stream defaults to True on construction
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    assert loop.stream is True


@pytest.mark.asyncio
async def test_query_loop_stream_metrics_fallback_estimate(mock_llm, tool_system):
    """When the streaming provider does not report usage, metrics should be
    estimated from content length so that tokens_per_second is non-zero.
    """
    async def mock_stream(*args, **kwargs):
        # Simulate a provider that never sends usage (common for Ollama, vLLM)
        yield LLMResponse(content="Hello ", finish_reason=None, usage=None)
        yield LLMResponse(content="world!", finish_reason="stop", usage=None)

    mock_llm.complete_stream.side_effect = mock_stream
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = [r async for r in loop.run("hi", stream=True)]
    final_results = [r for r in results if not r.is_status and not r.is_stream_chunk]
    assert len(final_results) == 1

    m = final_results[0].metrics
    assert m is not None
    # "Hello world!" is 12 chars -> ~3 tokens (12 // 4)
    assert m.completion_tokens == 3
    assert m.total_tokens == m.completion_tokens  # prompt_tokens stays 0
    assert m.tokens_per_second > 0.0

