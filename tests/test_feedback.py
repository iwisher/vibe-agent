"""Tests for vibe.harness.feedback and its integration with QueryLoop."""

from unittest.mock import AsyncMock

import pytest

from vibe.core.query_loop import QueryLoop, QueryState
from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.harness.feedback import FeedbackEngine, FeedbackResult
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
async def test_feedback_engine_self_verify(mock_llm):
    mock_llm.structured_output.return_value = {
        "score": 0.4,
        "issues": ["Missing type hints"],
        "suggested_fix": "Add type hints",
    }
    engine = FeedbackEngine(llm_client=mock_llm)
    result = await engine.self_verify("def foo(): pass")
    assert result.score == 0.4
    assert result.issues == ["Missing type hints"]
    assert result.suggested_fix == "Add type hints"


@pytest.mark.asyncio
async def test_feedback_engine_independent_evaluate(mock_llm):
    mock_llm.structured_output.return_value = {
        "score": 0.9,
        "issues": [],
        "suggested_fix": None,
    }
    engine = FeedbackEngine(llm_client=mock_llm)
    result = await engine.independent_evaluate("hello", {"clarity": "high"})
    assert result.score == 0.9
    assert result.issues == []


@pytest.mark.asyncio
async def test_feedback_engine_graceful_failure(mock_llm):
    mock_llm.structured_output.side_effect = Exception("boom")
    engine = FeedbackEngine(llm_client=mock_llm)
    result = await engine.self_verify("any output")
    assert result.score == 0.5
    assert "failed" in result.issues[0]


@pytest.mark.asyncio
async def test_query_loop_feedback_retry(mock_llm, tool_system):
    # First response triggers feedback, second response passes
    mock_llm.complete.side_effect = [
        LLMResponse(content="bad"),
        LLMResponse(content="good"),
    ]
    mock_llm.structured_output.side_effect = [
        {"score": 0.3, "issues": ["too short"], "suggested_fix": "Expand"},
        {"score": 0.9, "issues": [], "suggested_fix": None},
    ]

    engine = FeedbackEngine(llm_client=mock_llm)
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        feedback_engine=engine,
        feedback_threshold=0.7,
        max_feedback_retries=1,
    )
    results = [r async for r in loop.run("hi")]

    # First yield is the bad response with PROCESSING state (feedback injected)
    assert results[0].response == "bad"
    assert results[0].state == QueryState.PROCESSING
    # Second yield is the good response COMPLETED
    assert results[1].response == "good"
    assert results[1].state == QueryState.COMPLETED
    # Feedback system message was injected
    assert any("Feedback score" in m.content for m in loop.messages)


@pytest.mark.asyncio
async def test_query_loop_no_feedback_when_score_high(mock_llm, tool_system):
    mock_llm.complete.return_value = LLMResponse(content="good")
    mock_llm.structured_output.return_value = {
        "score": 0.95,
        "issues": [],
        "suggested_fix": None,
    }

    engine = FeedbackEngine(llm_client=mock_llm)
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        feedback_engine=engine,
        feedback_threshold=0.7,
    )
    results = [r async for r in loop.run("hi")]
    assert len(results) == 1
    assert results[0].state == QueryState.COMPLETED
