"""Tests for vibe.harness.orchestration.sync_delegate."""

from unittest.mock import AsyncMock, patch

import pytest

from vibe.harness.orchestration.sync_delegate import SyncDelegate, DelegateTask
from vibe.core.query_loop import QueryLoop
from vibe.core.model_gateway import LLMClient
from vibe.tools.tool_system import ToolSystem


@pytest.mark.asyncio
async def test_sync_delegate_runs_tasks():
    llm = AsyncMock(spec=LLMClient)
    llm.model = "test"
    llm.close = AsyncMock()

    # Mock QueryLoop.run to yield a single result
    async def mock_run(self, initial_query):
        from vibe.core.query_loop import QueryResult
        yield QueryResult(response="result for " + initial_query)

    with patch.object(QueryLoop, "run", mock_run):
        delegate = SyncDelegate(
            llm_client_factory=lambda: llm,
            tool_system_factory=lambda: ToolSystem(),
            max_workers=2,
        )
        tasks = [
            DelegateTask(description="task 1"),
            DelegateTask(description="task 2"),
        ]
        results = await delegate.run(tasks)

    assert len(results) == 2
    assert all(r.success for r in results)
    assert any("task 1" in r.output for r in results)
    assert any("task 2" in r.output for r in results)


@pytest.mark.asyncio
async def test_sync_delegate_timeout():
    import asyncio

    llm = AsyncMock(spec=LLMClient)
    llm.model = "test"
    llm.close = AsyncMock()

    async def slow_run(self, initial_query):
        await asyncio.sleep(10)
        from vibe.core.query_loop import QueryResult
        yield QueryResult(response="too late")

    with patch.object(QueryLoop, "run", slow_run):
        delegate = SyncDelegate(
            llm_client_factory=lambda: llm,
            tool_system_factory=lambda: ToolSystem(),
            max_workers=1,
        )
        tasks = [DelegateTask(description="slow", timeout_seconds=0.05)]
        results = await delegate.run(tasks)

    assert len(results) == 1
    assert not results[0].success
    assert "timed out" in results[0].error.lower()
