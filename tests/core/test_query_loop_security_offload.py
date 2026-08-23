"""Security evaluation offload in QueryLoop.

The interactive approval prompt blocks on user input, so
`QueryLoop._filter_tool_calls` runs `SecurityCoordinator.evaluate_tool_call`
via `asyncio.to_thread`. This keeps the event loop free for the
prompt_toolkit approval UI hook (see vibe/cli/main.py) while preserving the
security gate on tool execution.
"""

import threading
from unittest.mock import AsyncMock, MagicMock

from vibe.core.coordinators import SecurityCheckResult
from vibe.core.query_loop import QueryLoop
from vibe.tools.tool_system import ToolResult, ToolSystem


def _make_loop() -> QueryLoop:
    mock_llm = MagicMock()
    mock_llm.model = "test"
    return QueryLoop(llm_client=mock_llm, tool_system=ToolSystem())


def _bash_call(command: str) -> dict:
    return {"id": "c1", "function": {"name": "bash", "arguments": f'{{"command": "{command}"}}'}}


def _mock_executor(*results: ToolResult) -> MagicMock:
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=list(results))
    return executor


class TestSecurityGate:
    async def test_denial_blocks_tool_execution(self):
        loop = _make_loop()
        loop.security_coord = MagicMock()
        loop.security_coord.evaluate_tool_call = MagicMock(
            return_value=SecurityCheckResult(
                allowed=False, reason="User denied", layer="human_approval"
            )
        )
        loop.tool_executor = _mock_executor()

        results = await loop._execute_with_security([_bash_call("rm -rf /")])

        assert len(results) == 1
        assert not results[0].success
        assert "Security blocked" in results[0].error
        assert results[0].metadata["security_denial"] is True
        assert results[0].metadata["security_layer"] == "human_approval"
        loop.tool_executor.execute.assert_not_called()

    async def test_approval_allows_tool_execution(self):
        loop = _make_loop()
        loop.security_coord = MagicMock()
        loop.security_coord.evaluate_tool_call = MagicMock(
            return_value=SecurityCheckResult(allowed=True, reason="Approved for this execution")
        )
        ok = ToolResult(success=True, content="done")
        loop.tool_executor = _mock_executor(ok)

        results = await loop._execute_with_security([_bash_call("echo hi")])

        assert results == [ok]
        loop.tool_executor.execute.assert_called_once()

    async def test_denial_blocks_tool_execution_dag_path(self):
        loop = _make_loop()
        loop.security_coord = MagicMock()
        loop.security_coord.evaluate_tool_call = MagicMock(
            return_value=SecurityCheckResult(allowed=False, reason="User denied", layer="strict")
        )
        loop.tool_executor = _mock_executor()

        results = await loop._execute_tools_dag([_bash_call("rm -rf /")])

        assert len(results) == 1
        assert not results[0].success
        assert "Security blocked" in results[0].error
        loop.tool_executor.execute.assert_not_called()

    async def test_no_security_coordinator_passes_through(self):
        loop = _make_loop()
        loop.security_coord = None
        ok = ToolResult(success=True, content="done")
        loop.tool_executor = _mock_executor(ok)

        results = await loop._execute_with_security([_bash_call("echo hi")])

        assert results == [ok]


class TestEvaluationRunsOffLoopThread:
    async def test_evaluate_tool_call_runs_on_worker_thread(self):
        loop = _make_loop()
        seen: dict[str, int] = {}

        def eval_and_capture(name, args):
            seen["thread"] = threading.get_ident()
            return SecurityCheckResult(allowed=True)

        loop.security_coord = MagicMock()
        loop.security_coord.evaluate_tool_call = MagicMock(side_effect=eval_and_capture)
        loop.tool_executor = _mock_executor(ToolResult(success=True, content="ok"))

        loop_thread = threading.get_ident()
        await loop._execute_with_security([_bash_call("echo hi")])

        assert "thread" in seen
        assert seen["thread"] != loop_thread

    async def test_magicmock_coordinator_still_gates(self):
        """A plain MagicMock coordinator (common in existing tests) still works."""
        loop = _make_loop()
        denial = SecurityCheckResult(allowed=False, reason="nope", layer="human_approval")
        loop.security_coord = MagicMock()
        loop.security_coord.evaluate_tool_call = MagicMock(return_value=denial)
        loop.tool_executor = _mock_executor()

        results = await loop._execute_with_security([_bash_call("rm -rf /")])

        assert len(results) == 1
        assert not results[0].success
        loop.tool_executor.execute.assert_not_called()
