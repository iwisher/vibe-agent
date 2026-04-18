"""Tests for resource lifecycle and leak prevention."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.query_loop import QueryLoop
from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.tools.tool_system import ToolSystem


class FakeLLM(LLMClient):
    """Fake LLM that tracks close() calls."""

    def __init__(self):
        self.close_called = False
        self.model = "fake"

    async def complete(self, messages, tools=None, temperature=None):
        return LLMResponse(content="done")

    async def close(self):
        self.close_called = True


@pytest.fixture
def empty_tool_system():
    return ToolSystem()


@pytest.mark.asyncio
async def test_queryloop_close_calls_llm_close(empty_tool_system):
    """QueryLoop.close() must delegate to LLMClient.close()."""
    fake_llm = FakeLLM()
    loop = QueryLoop(llm_client=fake_llm, tool_system=empty_tool_system)
    assert not fake_llm.close_called
    await loop.close()
    assert fake_llm.close_called


@pytest.mark.asyncio
async def test_queryloop_close_calls_mcp_close(empty_tool_system):
    """QueryLoop.close() must close MCPBridge if present."""
    fake_llm = FakeLLM()
    mcp = MagicMock()
    mcp.close = AsyncMock()

    loop = QueryLoop(llm_client=fake_llm, tool_system=empty_tool_system, mcp_bridge=mcp)
    await loop.close()
    mcp.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_queryloop_close_idempotent(empty_tool_system):
    """Calling close() multiple times should not raise."""
    fake_llm = FakeLLM()
    loop = QueryLoop(llm_client=fake_llm, tool_system=empty_tool_system)
    await loop.close()
    await loop.close()  # should not raise
    assert fake_llm.close_called


@pytest.mark.asyncio
async def test_queryloop_close_with_none_llm(empty_tool_system):
    """close() should tolerate llm=None (edge case)."""
    loop = QueryLoop(llm_client=None, tool_system=empty_tool_system)
    await loop.close()  # should not raise
