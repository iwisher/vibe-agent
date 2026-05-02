"""Tests for EvalRunner."""

import tempfile
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.query_loop import QueryResult, QueryState
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalCase, EvalResult, EvalStore


async def _async_gen(items):
    for item in items:
        yield item


@pytest.fixture
def temp_eval_store(tmp_path):
    db_path = tmp_path / "evals.db"
    return EvalStore(db_path=str(db_path), evals_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_runner_file_exists_pass(tmp_path):
    test_file = tmp_path / "exists.txt"
    test_file.write_text("hello")

    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(return_value=_async_gen([QueryResult(response="done", state=QueryState.COMPLETED)]))

    runner = EvalRunner(query_loop=ql)
    case = EvalCase(
        id="test-001",
        tags=["file"],
        input={"prompt": "create file"},
        expected={"file_exists": str(test_file)},
    )
    result = await runner.run_case(case)
    assert result.passed is True
    assert result.diff == {}


@pytest.mark.asyncio
async def test_runner_file_exists_fail():
    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(return_value=_async_gen([QueryResult(response="done", state=QueryState.COMPLETED)]))

    runner = EvalRunner(query_loop=ql)
    case = EvalCase(
        id="test-002",
        tags=["file"],
        input={"prompt": "create file"},
        expected={"file_exists": "/nonexistent/path/xyz.txt"},
    )
    result = await runner.run_case(case)
    assert result.passed is False
    assert "file_exists" in result.diff


@pytest.mark.asyncio
async def test_runner_contains_text_pass(tmp_path):
    test_file = tmp_path / "data.txt"
    test_file.write_text("hello world")

    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(return_value=_async_gen([QueryResult(response="done", state=QueryState.COMPLETED)]))

    runner = EvalRunner(query_loop=ql)
    case = EvalCase(
        id="test-003",
        tags=["file"],
        input={"prompt": "write file"},
        expected={"file_contains": str(test_file), "contains_text": "hello world"},
    )
    result = await runner.run_case(case)
    assert result.passed is True


@pytest.mark.asyncio
async def test_runner_stdout_contains_pass():
    from vibe.tools.tool_system import ToolResult

    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(
        return_value=_async_gen([
            QueryResult(
                response="",
                tool_results=[ToolResult(success=True, content="the answer is 42")],
                state=QueryState.SYNTHESIZING,
            ),
            QueryResult(response="done", state=QueryState.COMPLETED),
        ])
    )

    runner = EvalRunner(query_loop=ql)
    case = EvalCase(
        id="test-004",
        tags=["bash"],
        input={"prompt": "run command"},
        expected={"stdout_contains": "42"},
    )
    result = await runner.run_case(case)
    assert result.passed is True


@pytest.mark.asyncio
async def test_runner_response_contains_pass():
    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(return_value=_async_gen([QueryResult(response="blocked by policy", state=QueryState.COMPLETED)]))

    runner = EvalRunner(query_loop=ql)
    case = EvalCase(
        id="test-005",
        tags=["security"],
        input={"prompt": "try dangerous"},
        expected={"response_contains": "blocked"},
    )
    result = await runner.run_case(case)
    assert result.passed is True


@pytest.mark.asyncio
async def test_runner_records_result(temp_eval_store):
    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(return_value=_async_gen([QueryResult(response="done", state=QueryState.COMPLETED)]))

    runner = EvalRunner(query_loop=ql, eval_store=temp_eval_store)
    case = EvalCase(
        id="test-006",
        tags=["basic"],
        input={"prompt": "hello"},
        expected={},
    )
    result = await runner.run_case(case)
    assert result.passed is True
    summary = temp_eval_store.summary()
    assert summary["total_runs"] == 1


@pytest.mark.asyncio
async def test_runner_run_all():
    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.close = AsyncMock()
    ql.copy = MagicMock(return_value=ql)
    ql.run = MagicMock(return_value=_async_gen([QueryResult(response="done", state=QueryState.COMPLETED)]))

    runner = EvalRunner(query_loop=ql)
    cases = [
        EvalCase(id="a", tags=[], input={}, expected={}),
        EvalCase(id="b", tags=[], input={}, expected={}),
    ]
    results = await runner.run_all(cases)
    assert len(results) == 2
    assert results[0].eval_id == "a"
    assert results[1].eval_id == "b"


@pytest.mark.asyncio
async def test_runner_no_results():
    ql = MagicMock()
    ql.clear_history = MagicMock()
    ql.run = MagicMock(return_value=_async_gen([]))

    runner = EvalRunner(query_loop=ql)
    case = EvalCase(id="empty", tags=[], input={}, expected={})
    result = await runner.run_case(case)
    assert result.passed is False
    assert result.diff.get("reason") == "No results produced"
