"""Tests for EvalRunner assertion helpers (tool_sequence, no_tool_called, metrics_threshold)."""

from unittest.mock import AsyncMock

import pytest

from vibe.core.model_gateway import LLMClient
from vibe.core.query_loop import Message, QueryLoop, QueryResult, QueryState
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalCase, EvalResult
from vibe.tools.tool_system import ToolSystem


@pytest.fixture
def mock_llm():
    m = AsyncMock(spec=LLMClient)
    m.model = "test-model"
    return m


@pytest.fixture
def empty_tool_system():
    return ToolSystem()


async def _run_case_with_messages(query_loop, case: EvalCase) -> EvalResult:
    """Helper: monkeypatch run() to skip side effects and preserve seeded messages."""
    runner = EvalRunner(query_loop=query_loop)

    async def fake_run(initial_query):
        yield QueryResult(response="done", state=QueryState.COMPLETED)

    query_loop.run = fake_run
    query_loop.clear_history = lambda: None  # prevent clearing seeded messages
    return await runner.run_case(case)


# ─── tool_sequence assertions ───

@pytest.mark.asyncio
async def test_assert_tool_sequence_pass(mock_llm, empty_tool_system):
    """tool_sequence matches exact order → pass."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)
    loop.messages = [
        Message(role="assistant", content="", tool_calls=[
            {"function": {"name": "read_file"}}
        ]),
        Message(role="assistant", content="", tool_calls=[
            {"function": {"name": "bash_tool"}}
        ]),
    ]

    case = EvalCase(
        id="seq_001",
        tags=["test"],
        input={"prompt": "test"},
        expected={"tool_sequence": ["read_file", "bash_tool"]},
    )
    result = await _run_case_with_messages(loop, case)
    assert result.passed is True
    assert "tool_sequence" not in result.diff


@pytest.mark.asyncio
async def test_assert_tool_sequence_fail(mock_llm, empty_tool_system):
    """tool_sequence out of order → fail."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)
    loop.messages = [
        Message(role="assistant", content="", tool_calls=[
            {"function": {"name": "bash_tool"}}
        ]),
        Message(role="assistant", content="", tool_calls=[
            {"function": {"name": "read_file"}}
        ]),
    ]

    case = EvalCase(
        id="seq_002",
        tags=["test"],
        input={"prompt": "test"},
        expected={"tool_sequence": ["read_file", "bash_tool"]},
    )
    result = await _run_case_with_messages(loop, case)
    assert result.passed is False
    assert "tool_sequence" in result.diff
    assert "bash_tool" in result.diff["tool_sequence"]


# ─── no_tool_called assertions ───

@pytest.mark.asyncio
async def test_assert_no_tool_called_pass(mock_llm, empty_tool_system):
    """No tools invoked → pass."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)
    case = EvalCase(
        id="no_tool_001",
        tags=["test"],
        input={"prompt": "hi"},
        expected={"no_tool_called": True},
    )
    result = await _run_case_with_messages(loop, case)
    assert result.passed is True
    assert "no_tool_called" not in result.diff


@pytest.mark.asyncio
async def test_assert_no_tool_called_fail(mock_llm, empty_tool_system):
    """Tools were invoked but expected none → fail."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)

    async def fake_run_with_tools(initial_query):
        yield QueryResult(
            response="",
            tool_results=[],
            state=QueryState.COMPLETED,
        )

    loop.run = fake_run_with_tools
    runner = EvalRunner(query_loop=loop)

    # Seed results with tool_results by using a real-ish run that yields tool results
    async def fake_run_with_tool_results(initial_query):
        from vibe.tools.tool_system import ToolResult
        yield QueryResult(
            response="",
            tool_results=[ToolResult(success=True, content="ok")],
            state=QueryState.COMPLETED,
        )

    loop.run = fake_run_with_tool_results
    case = EvalCase(
        id="no_tool_002",
        tags=["test"],
        input={"prompt": "do it"},
        expected={"no_tool_called": True},
    )
    result = await runner.run_case(case)
    assert result.passed is False
    assert "no_tool_called" in result.diff


# ─── metrics_threshold assertions ───

@pytest.mark.asyncio
async def test_assert_metrics_threshold_pass(mock_llm, empty_tool_system):
    """Latency and tokens within budget → pass."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)
    case = EvalCase(
        id="metrics_001",
        tags=["test"],
        input={"prompt": "hi"},
        expected={"metrics_threshold": {"max_latency_seconds": 10.0, "max_total_tokens": 1000}},
    )
    result = await _run_case_with_messages(loop, case)
    assert result.passed is True
    assert result.latency_seconds >= 0
    assert "metrics_threshold" not in result.diff


@pytest.mark.asyncio
async def test_assert_metrics_threshold_fail_latency(mock_llm, empty_tool_system):
    """Latency exceeds max_latency_seconds → fail."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)
    case = EvalCase(
        id="metrics_002",
        tags=["test"],
        input={"prompt": "hi"},
        expected={"metrics_threshold": {"max_latency_seconds": 0.0}},  # any positive latency fails
    )
    result = await _run_case_with_messages(loop, case)
    assert result.passed is False
    assert "metrics_threshold" in result.diff
    assert "Latency" in result.diff["metrics_threshold"]


@pytest.mark.asyncio
async def test_assert_metrics_threshold_fail_tokens(mock_llm, empty_tool_system):
    """Total tokens exceed max_total_tokens → fail."""
    loop = QueryLoop(llm_client=mock_llm, tool_system=empty_tool_system)

    async def fake_run_with_tokens(initial_query):
        yield QueryResult(
            response="ok",
            metrics=type("M", (), {
                "total_tokens": 800,
                "prompt_tokens": 500,
                "completion_tokens": 300,
            })(),
            state=QueryState.COMPLETED,
        )

    loop.run = fake_run_with_tokens
    runner = EvalRunner(query_loop=loop)
    case = EvalCase(
        id="metrics_003",
        tags=["test"],
        input={"prompt": "hi"},
        expected={"metrics_threshold": {"max_total_tokens": 100}},  # too low
    )
    result = await runner.run_case(case)
    assert result.passed is False
    assert "metrics_threshold" in result.diff
    assert "tokens" in result.diff["metrics_threshold"]
