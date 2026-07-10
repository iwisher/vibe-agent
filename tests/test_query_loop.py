"""Tests for vibe.core.query_loop."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.core.query_loop import QueryLoop, QueryState
from vibe.harness.constraints import HookPipeline, permission_gate_hook, policy_hook
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
async def test_run_simple_response(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(content="hello")
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    results = [r async for r in loop.run("hi") if not r.is_status]
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
    results = [r async for r in loop.run("do it") if not r.is_status]
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
    results = [r async for r in loop.run("hi") if not r.is_status]
    assert results[0].error is not None
    assert results[0].state == QueryState.ERROR


def test_query_result_status_fields():
    from vibe.core.query_loop import QueryResult

    qr = QueryResult(is_status=True, status_message="Testing...")
    assert qr.is_status is True
    assert qr.status_message == "Testing..."


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
    results = [r async for r in loop.run("do it") if not r.is_status]
    assert results[0].tool_results[0].success is False
    assert "Hook veto" in results[0].tool_results[0].error


@pytest.mark.asyncio
async def test_hook_pipeline_policy_block(mock_llm, tool_system):
    from vibe.tools.bash import BashSandbox, BashTool

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
    results = [r async for r in loop.run("do it") if not r.is_status]
    assert results[0].tool_results[0].success is False
    assert (
        "blocked" in results[0].tool_results[0].error.lower()
        or "policy" in results[0].tool_results[0].error.lower()
    )


@pytest.mark.asyncio
async def test_stop_loop(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(content="ok")
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    loop.stop()
    results = [r async for r in loop.run("hi") if not r.is_status]
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
    results = [r async for r in loop.run("use the dummy tool") if not r.is_status]
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
        Skill(
            name="rust_guru",
            description="Rust expert",
            content="You are a Rust expert.",
            tags=["rust"],
        ),
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
    results = [r async for r in loop.run("help with rust") if not r.is_status]
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
    results = [r async for r in loop.run("something completely unrelated") if not r.is_status]
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
    results = [r async for r in loop.run("open the browser") if not r.is_status]
    assert len(results) == 1
    assert loop.messages[0].role == "system"
    assert "browser" in loop.messages[0].content
    assert "fs" not in loop.messages[0].content


@pytest.mark.asyncio
async def test_query_loop_yields_status(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(content="hello")
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = []
    async for res in loop.run("test query"):
        results.append(res)

    status_updates = [r for r in results if r.is_status]
    assert len(status_updates) > 0
    assert any("Planning" in r.status_message for r in status_updates)
    assert any("Waiting for test-model" in r.status_message for r in status_updates)


@pytest.mark.asyncio
async def test_query_loop_yields_tool_status(mock_llm, tool_system):
    mock_llm.complete.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[{"id": "call_1", "name": "dummy", "arguments": "{}"}],
        ),
        LLMResponse(content="done"),
    ]
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    results = []
    async for res in loop.run("test tool"):
        results.append(res)

    status_updates = [r for r in results if r.is_status]
    assert any("Executing tools: ['dummy']" in r.status_message for r in status_updates)


@pytest.mark.asyncio
async def test_session_id_generated_before_first_yield(mock_llm, tool_system):
    """Session ID must be set before the first status result is yielded."""
    mock_llm.complete.return_value = LLMResponse(content="hello")
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)

    first_result = None
    async for res in loop.run("test query"):
        first_result = res
        break  # Only check the first yielded result

    assert first_result is not None
    assert first_result.is_status is True
    assert loop._session_id is not None
    assert len(loop._session_id) == 36  # UUID4 string length


@pytest.mark.asyncio
async def test_cost_router_switches_model(mock_llm, tool_system):
    """CostRouter should trigger model switch when it returns a different model."""
    from vibe.core.cost_router import RoutingDecision

    mock_router = MagicMock()
    mock_router.route.return_value = RoutingDecision(
        provider_name="test-provider",
        model_id="cheaper-model",
        tier="budget",
        estimated_cost=0.01,
        reason="test routing",
    )

    mock_llm.complete.return_value = LLMResponse(content="hello")
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        cost_router=mock_router,
    )
    results = [r async for r in loop.run("hi") if not r.is_status]
    assert len(results) == 1
    assert results[0].response == "hello"
    # CostRouter should have been consulted
    mock_router.route.assert_called_once()
    # Model should have been switched
    assert mock_llm.model == "cheaper-model"


@pytest.mark.asyncio
async def test_dag_execution_parallelizes_tools(mock_llm, tool_system):
    """DAG execution path should handle multiple independent tool calls."""
    from vibe.harness.dag_planner import DAGPlanner

    planner = DAGPlanner()

    mock_llm.complete.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "dummy", "arguments": "{}"},
                {"id": "call_2", "name": "dummy", "arguments": "{}"},
            ],
        ),
        LLMResponse(content="done"),
    ]

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        dag_planner=planner,
        enable_dag_execution=True,
    )
    results = [r async for r in loop.run("do two things") if not r.is_status]
    assert len(results) == 2
    # Both tool calls should succeed
    assert results[0].tool_results[0].success
    assert results[0].tool_results[1].success
    assert results[1].response == "done"


@pytest.mark.asyncio
async def test_dag_fallback_to_sequential_for_single_tool(mock_llm, tool_system):
    """Single tool calls should not take the DAG path even when enabled."""
    from vibe.harness.dag_planner import DAGPlanner

    planner = DAGPlanner()

    mock_llm.complete.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[{"id": "call_1", "name": "dummy", "arguments": "{}"}],
        ),
        LLMResponse(content="done"),
    ]

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        dag_planner=planner,
        enable_dag_execution=True,
    )
    results = [r async for r in loop.run("do one thing") if not r.is_status]
    assert len(results) == 2
    assert results[0].tool_results[0].success
    assert results[1].response == "done"


@pytest.mark.asyncio
async def test_run_streaming_simple(mock_llm, tool_system):
    async def mock_stream(*args, **kwargs):
        yield LLMResponse(content="hello", finish_reason=None)
        yield LLMResponse(content=" world", finish_reason="stop")

    mock_llm.complete_stream.side_effect = mock_stream
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    results = [r async for r in loop.run("hi", stream=True) if not r.is_status]

    assert len(results) >= 2
    assert results[0].response == "hello"
    assert results[1].response == " world"
    assert results[-1].response == "hello world"
    assert results[-1].state == QueryState.COMPLETED


@pytest.mark.asyncio
async def test_keyboard_interrupt_offers_rollback_hint(mock_llm, tool_system, caplog):
    """When KeyboardInterrupt interrupts the loop, a rollback hint is logged if a shadow exists."""
    from unittest.mock import patch

    from vibe.tools.git_shadow import ShadowBranch

    async def mock_stream(*args, **kwargs):
        raise KeyboardInterrupt()
        yield  # makes this an async generator

    mock_llm.complete_stream.side_effect = mock_stream

    fake_session_id = "test-session-id-1234"
    shadow_manager = MagicMock()
    shadow_manager.list_shadows.return_value = [
        ShadowBranch(
            session_id=fake_session_id,
            branch_name="vibe/shadow-test-session-id-1234",
            created_at="2024-01-01T00:00:00+00:00",
            original_branch="main",
            has_uncommitted_changes=False,
        )
    ]

    logger = logging.getLogger("test_query_loop")
    with patch("vibe.core.query_loop.uuid.uuid4", return_value=fake_session_id):
        loop = QueryLoop(
            llm_client=mock_llm,
            tool_system=tool_system,
            shadow_manager=shadow_manager,
            logger=logger,
        )
        with caplog.at_level(logging.INFO, logger="test_query_loop"):
            try:
                async for _ in loop.run("hi", stream=True):
                    pass
            except KeyboardInterrupt:
                pass

    assert loop.state != QueryState.COMPLETED
    shadow_manager.list_shadows.assert_called_once()
    assert "Rollback available: vibe/shadow-test-session-id-1234" in caplog.text


@pytest.mark.asyncio
async def test_error_run_offers_rollback_hint(mock_llm, tool_system, caplog):
    """When a run ends in ERROR and a shadow exists, a rollback hint is logged."""
    import logging
    import uuid

    mock_llm.complete.side_effect = RuntimeError("simulated failure")

    # Use a deterministic session ID so the shadow mock matches
    fixed_uuid = uuid.uuid4()
    shadow_manager = MagicMock()
    shadow_manager.list_shadows.return_value = [
        MagicMock(
            session_id=str(fixed_uuid),
            branch_name="vibe/shadow-test-sess-123",
            created_at=1,
        )
    ]

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        shadow_manager=shadow_manager,
    )
    # Set logger to capture output
    loop.logger = logging.getLogger("test_rollback")
    loop.logger.setLevel(logging.INFO)

    with patch("uuid.uuid4", return_value=fixed_uuid):
        with caplog.at_level(logging.INFO, logger="test_rollback"):
            results = [r async for r in loop.run("hi") if not r.is_status]

    assert results[0].state == QueryState.ERROR
    shadow_manager.list_shadows.assert_called_once()
    # The rollback hint should mention the branch name
    assert any("Rollback available" in rec.message for rec in caplog.records)
    assert any("vibe/shadow-test-sess-123" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_completed_run_skips_rollback_hint(mock_llm, tool_system, caplog):
    """When a run completes successfully, no rollback hint is logged."""
    import logging

    mock_llm.complete.return_value = LLMResponse(content="success")

    shadow_manager = MagicMock()
    shadow_manager.list_shadows.return_value = [
        MagicMock(session_id="test-sess-456", branch_name="vibe/shadow-test-sess-456", created_at=1)
    ]

    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        shadow_manager=shadow_manager,
    )
    loop.logger = logging.getLogger("test_no_rollback")
    loop.logger.setLevel(logging.INFO)

    with caplog.at_level(logging.INFO, logger="test_no_rollback"):
        results = [r async for r in loop.run("hi") if not r.is_status]

    assert results[0].state == QueryState.COMPLETED
    # list_shadows should NOT be called for successful runs
    shadow_manager.list_shadows.assert_not_called()
    assert not any("Rollback available" in rec.message for rec in caplog.records)


def test_copy_creates_fresh_compactor(mock_llm, tool_system):
    """Regression: copy() must not assume self.compactor is a CompactionCoordinator."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    copied = loop.copy()

    assert copied.compactor is not None
    assert copied.compactor is not loop.compactor
    assert copied.compaction_coord is not loop.compaction_coord
    assert copied.compaction_coord.compactor is copied.compactor
