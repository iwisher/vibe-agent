"""Edge-case and stress tests for QueryLoop."""

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
def empty_tool_system():
    return ToolSystem()


@pytest.fixture
def tool_system_with_dummy():
    ts = ToolSystem()
    ts.register_tool(DummyTool("dummy", "dummy tool"))
    return ts


# ─── edge_001: Empty tool list ───


@pytest.mark.asyncio
async def test_edge_001_empty_tool_list_does_not_crash(mock_llm, empty_tool_system):
    """edge_001: QueryLoop with no tools registered should not crash."""
    mock_llm.complete.return_value = LLMResponse(content="Hello, I can help you.")

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=empty_tool_system,
        max_iterations=5,
    )
    all_results = [r async for r in loop.run("hi")]
    results = [r for r in all_results if not r.is_status]

    assert len(results) >= 1
    assert results[-1].state == QueryState.COMPLETED
    assert "Hello, I can help you" in results[-1].response
    # LLM was called with empty tools list
    call_args = mock_llm.complete.call_args
    assert call_args[1].get("tools") == []


# ─── edge_002: Malformed tool arguments ───


@pytest.mark.asyncio
async def test_edge_002_malformed_tool_args_graceful_error(mock_llm, tool_system_with_dummy):
    """edge_002: Malformed JSON in tool arguments yields graceful ToolResult error."""
    mock_llm.complete.return_value = LLMResponse(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "dummy",
                    "arguments": "{invalid json",  # malformed
                },
            }
        ],
    )

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system_with_dummy,
        max_iterations=5,
    )
    all_results = [r async for r in loop.run("do something")]
    results = [r for r in all_results if not r.is_status]

    # Should yield a result with tool_results containing the error
    assert len(results) >= 1
    # Find first result that has tool_results or an error
    result_with_tools = None
    for r in results:
        if r.tool_results or r.error:
            result_with_tools = r
            break
    assert result_with_tools is not None
    # Malformed args cause error result, not tool_results
    if len(result_with_tools.tool_results) == 0 and result_with_tools.error:
        assert (
            "json" in str(result_with_tools.error).lower()
            or "expecting" in str(result_with_tools.error).lower()
        )
    else:
        tool_results = result_with_tools.tool_results
        assert len(tool_results) == 1
        assert tool_results[0].success is False
        assert (
            "json" in tool_results[0].error.lower() or "expecting" in tool_results[0].error.lower()
        )


# ─── edge_003: Max iteration exhaustion ───


@pytest.mark.asyncio
async def test_edge_003_max_iteration_exhaustion_returns_partial(mock_llm, tool_system_with_dummy):
    """edge_003: When max_iterations is reached, loop stops and returns partial results."""
    # LLM always wants to call a tool → infinite loop unless capped
    mock_llm.complete.return_value = LLMResponse(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "dummy", "arguments": "{}"},
            }
        ],
    )

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system_with_dummy,
        max_iterations=2,
    )
    all_results = [r async for r in loop.run("infinite tools")]
    results = [r for r in all_results if not r.is_status]

    # Should have exactly 2 iterations worth of results
    assert len(results) == 2
    for r in results:
        assert len(r.tool_results) == 1
        assert r.tool_results[0].success is True

    # After exhaustion the state is set to INCOMPLETE (distinguishes from natural completion)
    assert loop.state == QueryState.INCOMPLETE
    # LLM was called exactly max_iterations times
    assert mock_llm.complete.call_count == 2
