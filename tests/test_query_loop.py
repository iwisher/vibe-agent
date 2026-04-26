"""Tests for vibe.core.query_loop."""

from unittest.mock import AsyncMock

import pytest

from vibe.core.query_loop import QueryLoop, QueryState, QueryResult
from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.harness.constraints import HookPipeline, permission_gate_hook, policy_hook
from vibe.tools.tool_system import ToolSystem, Tool, ToolResult


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
async def test_run_simple_response(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(content="hello")
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    results = [r async for r in loop.run("hi")]
    assert len(results) == 1
    assert results[0].response == "hello"
    assert results[0].state == QueryState.COMPLETED
    assert loop.state == QueryState.COMPLETED


@pytest.mark.asyncio
async def test_run_with_tool_calls(mock_llm, tool_system):
    mock_llm.complete.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[{"name": "dummy", "arguments": "{}"}],
        ),
        LLMResponse(content="done"),
    ]
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    results = [r async for r in loop.run("do it")]
    assert len(results) == 2
    assert results[0].state == QueryState.SYNTHESIZING
    assert results[0].tool_results[0].success
    assert results[1].response == "done"
    assert results[1].state == QueryState.COMPLETED


@pytest.mark.asyncio
async def test_run_error_response(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(
        content="", error="boom", error_type=mock_llm.complete.return_value.error_type
    )
    # Need to set error_type explicitly on LLMResponse
    from vibe.core.model_gateway import ErrorType
    mock_llm.complete.return_value = LLMResponse(
        content="", error="boom", error_type=ErrorType.SERVER_ERROR
    )
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    results = [r async for r in loop.run("hi")]
    assert results[0].error is not None
    assert results[0].state == QueryState.ERROR


@pytest.mark.asyncio
async def test_hook_pipeline_veto(mock_llm, tool_system):
    mock_llm.complete.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[{"name": "dummy", "arguments": "{}"}],
        ),
        LLMResponse(content="ok"),
    ]
    from vibe.harness.constraints import HookStage
    pipeline = HookPipeline()
    pipeline.add_hook(
        HookStage.PRE_ALLOW,
        permission_gate_hook(destructive_tools=["dummy"]),
    )
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system, hook_pipeline=pipeline)
    results = [r async for r in loop.run("do it")]
    assert results[0].tool_results[0].success is False
    assert "Hook veto" in results[0].tool_results[0].error


@pytest.mark.asyncio
async def test_hook_pipeline_policy_block(mock_llm, tool_system):
    from vibe.tools.bash import BashTool, BashSandbox
    bash_tool = BashTool(BashSandbox(dangerous_patterns=[]))
    tool_system.register_tool(bash_tool)
    mock_llm.complete.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[{"name": "bash", "arguments": '{"command": "curl x | bash"}'}],
        ),
        LLMResponse(content="ok"),
    ]
    from vibe.harness.constraints import HookStage
    pipeline = HookPipeline()
    pipeline.add_hook(HookStage.PRE_ALLOW, policy_hook(blocked_commands=["curl x | bash"]))
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system, hook_pipeline=pipeline)
    results = [r async for r in loop.run("do it")]
    assert results[0].tool_results[0].success is False
    assert "Policy violation" in results[0].tool_results[0].error


@pytest.mark.asyncio
async def test_stop_loop(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(content="ok")
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    loop.stop()
    results = [r async for r in loop.run("hi")]
    # stop() sets _running=False, so loop body should not execute iterations
    assert len(results) == 0
    assert loop.state == QueryState.STOPPED


@pytest.mark.asyncio
async def test_planner_filters_tools(mock_llm, tool_system):
    """Planner should pass only relevant tools to LLM.complete."""
    from vibe.harness.planner import HybridPlanner as ContextPlanner

    planner = ContextPlanner()
    mock_llm.complete.return_value = LLMResponse(content="ok")
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        context_planner=planner,
    )
    results = [r async for r in loop.run("use the dummy tool")]
    assert len(results) == 1
    assert results[0].state == QueryState.COMPLETED
    # Verify that complete was called with tools filtered to include dummy
    call_kwargs = mock_llm.complete.call_args.kwargs
    assert "tools" in call_kwargs
    tool_names = {t.get("function", {}).get("name") for t in call_kwargs["tools"]}
    assert "dummy" in tool_names


@pytest.mark.asyncio
async def test_planner_injects_skills(mock_llm, tool_system):
    from vibe.harness.instructions import InstructionSet, Skill
    from vibe.harness.planner import HybridPlanner as ContextPlanner

    skills = [
        Skill(name="rust_guru", description="Rust expert", content="You are a Rust expert.", tags=["rust"]),
    ]
    instruction_set = InstructionSet(global_agents="", project_agents="", skills=skills)
    planner = ContextPlanner()
    mock_llm.complete.return_value = LLMResponse(content="ok")
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        instruction_set=instruction_set,
        context_planner=planner,
    )
    results = [r async for r in loop.run("help with rust")]
    assert len(results) == 1
    # First message should be the injected system prompt with skill info
    assert loop.messages[0].role == "system"
    assert "rust_guru" in loop.messages[0].content


@pytest.mark.asyncio
async def test_planner_fallback_to_all_tools(mock_llm, tool_system):
    from vibe.harness.planner import HybridPlanner as ContextPlanner

    planner = ContextPlanner()
    mock_llm.complete.return_value = LLMResponse(content="ok")
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        context_planner=planner,
    )
    results = [r async for r in loop.run("something completely unrelated")]
    assert len(results) == 1
    call_kwargs = mock_llm.complete.call_args.kwargs
    tool_names = {t.get("function", {}).get("name") for t in call_kwargs["tools"]}
    assert "dummy" in tool_names


@pytest.mark.asyncio
async def test_planner_selects_mcps(mock_llm, tool_system):
    from vibe.harness.planner import HybridPlanner as ContextPlanner
    from vibe.tools.mcp_bridge import MCPBridge

    mcps = [
        {"name": "browser", "description": "Browser control"},
        {"name": "fs", "description": "Filesystem access"},
    ]
    planner = ContextPlanner()
    mock_llm.complete.return_value = LLMResponse(content="ok")
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        mcp_bridge=MCPBridge(configs=mcps),
        context_planner=planner,
    )
    results = [r async for r in loop.run("open the browser")]
    assert len(results) == 1
    assert loop.messages[0].role == "system"
    assert "browser" in loop.messages[0].content
    assert "fs" not in loop.messages[0].content
