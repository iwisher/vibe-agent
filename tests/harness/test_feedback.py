"""Tests for structured FeedbackEngine."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from vibe.harness.feedback import (
    BatchFeedbackResult,
    FeedbackEngine,
    FeedbackResult,
    FeedbackSchema,
    FeedbackStatus,
)
from vibe.core.coordinators import FeedbackCoordinator


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response_data: dict):
        self._response = response_data

    async def structured_output(self, messages, output_schema, temperature=0.1):
        return self._response


class TestFeedbackSchema:
    """Test Pydantic feedback schema."""

    def test_valid_schema(self):
        """Should validate correct data."""
        schema = FeedbackSchema(score=0.8, issues=["issue1"], confidence=0.9)
        assert schema.score == 0.8
        assert schema.issues == ["issue1"]
        assert schema.status == FeedbackStatus.OK

    def test_score_validation(self):
        """Should validate score range."""
        with pytest.raises(ValueError):
            FeedbackSchema(score=1.5)

        with pytest.raises(ValueError):
            FeedbackSchema(score=-0.1)

    def test_default_values(self):
        """Should have sensible defaults."""
        schema = FeedbackSchema(score=0.5)
        assert schema.confidence == 0.8
        assert schema.issues == []
        assert schema.category_scores == {}
        assert schema.status == FeedbackStatus.OK

    def test_status_override(self):
        """Should allow explicit status."""
        schema = FeedbackSchema(score=0.5, status=FeedbackStatus.BELOW_THRESHOLD)
        assert schema.status == FeedbackStatus.BELOW_THRESHOLD


class TestFeedbackResult:
    """Test FeedbackResult dataclass."""

    def test_from_pydantic(self):
        """Should convert from Pydantic schema."""
        schema = FeedbackSchema(
            score=0.9,
            issues=["good"],
            suggested_fix="fix it",
            confidence=0.95,
            category_scores={"correctness": 0.9},
        )
        result = FeedbackResult.from_pydantic(schema)
        assert result.score == 0.9
        assert result.issues == ["good"]
        assert result.suggested_fix == "fix it"
        assert result.confidence == 0.95
        assert result.category_scores == {"correctness": 0.9}
        assert result.status == FeedbackStatus.OK

    def test_default_status(self):
        """Should default to OK."""
        result = FeedbackResult()
        assert result.status == FeedbackStatus.OK


class TestFeedbackEngine:
    """Test FeedbackEngine."""

    @pytest.mark.asyncio
    async def test_self_verify(self):
        """Should self-verify output."""
        mock_response = {
            "score": 0.8,
            "issues": ["minor issue"],
            "suggested_fix": "fix it",
            "confidence": 0.9,
            "category_scores": {"correctness": 0.8},
        }
        llm = MockLLMClient(mock_response)
        engine = FeedbackEngine(llm)

        result = await engine.self_verify("test output")
        assert result.score == 0.8
        assert "minor issue" in result.issues
        assert result.status == FeedbackStatus.OK

    @pytest.mark.asyncio
    async def test_independent_evaluate(self):
        """Should evaluate with rubric."""
        mock_response = {
            "score": 0.9,
            "issues": [],
            "confidence": 0.95,
            "category_scores": {"correctness": 0.9, "clarity": 0.9},
        }
        llm = MockLLMClient(mock_response)
        engine = FeedbackEngine(llm)

        rubric = {"correctness": "Is it correct?"}
        result = await engine.independent_evaluate("test output", rubric)
        assert result.score == 0.9
        assert result.category_scores == {"correctness": 0.9, "clarity": 0.9}
        assert result.status == FeedbackStatus.OK

    @pytest.mark.asyncio
    async def test_batch_evaluate(self):
        """Should evaluate multiple outputs."""
        mock_response = {
            "score": 0.7,
            "issues": ["common issue"],
            "confidence": 0.8,
            "category_scores": {},
        }
        llm = MockLLMClient(mock_response)
        engine = FeedbackEngine(llm)

        outputs = ["output1", "output2", "output3"]
        result = await engine.batch_evaluate(outputs)

        assert len(result.results) == 3
        assert result.aggregate_score == pytest.approx(0.7, abs=0.01)
        assert "common issue" in result.common_issues
        for r in result.results:
            assert r.status == FeedbackStatus.OK

    @pytest.mark.asyncio
    async def test_feedback_failure_fallback(self):
        """Should return ENGINE_ERROR on LLM failure."""
        llm = MagicMock()
        llm.structured_output = AsyncMock(side_effect=Exception("LLM error"))
        engine = FeedbackEngine(llm)

        result = await engine.self_verify("test")
        assert result.score == 0.5
        assert "Feedback evaluation failed" in result.issues[0]
        assert result.confidence == 0.0
        assert result.status == FeedbackStatus.ENGINE_ERROR

    @pytest.mark.asyncio
    async def test_validation_error_fallback(self):
        """Should return VALIDATION_ERROR on Pydantic validation failure."""
        llm = MockLLMClient({
            "score": 2.0,  # Out of range, will fail validation
            "issues": ["bad"],
        })
        engine = FeedbackEngine(llm)

        result = await engine.self_verify("test")
        assert result.score == 2.0
        assert result.issues == ["bad"]
        assert result.status == FeedbackStatus.VALIDATION_ERROR

    def test_default_rubric(self):
        """Should have default rubric."""
        llm = MockLLMClient({})
        engine = FeedbackEngine(llm)

        assert "correctness" in engine.DEFAULT_RUBRIC
        assert "completeness" in engine.DEFAULT_RUBRIC
        assert "clarity" in engine.DEFAULT_RUBRIC
        assert "safety" in engine.DEFAULT_RUBRIC

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        """Should handle empty batch."""
        llm = MockLLMClient({})
        engine = FeedbackEngine(llm)

        result = await engine.batch_evaluate([])
        assert len(result.results) == 0
        assert result.aggregate_score == 0.0


class TestFeedbackCoordinator:
    """Test FeedbackCoordinator status logic."""

    @pytest.mark.asyncio
    async def test_ok_above_threshold(self):
        """Should return should_continue=False when score is above threshold."""
        llm = MockLLMClient({"score": 0.9, "issues": [], "confidence": 0.9})
        engine = FeedbackEngine(llm)
        coord = FeedbackCoordinator(engine, threshold=0.7)

        should_continue, hint = await coord.evaluate("test content")
        assert should_continue is False
        assert hint is None

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        """Should set BELOW_THRESHOLD and return retry hint when score is low."""
        llm = MockLLMClient({"score": 0.4, "issues": ["too short"], "confidence": 0.8})
        engine = FeedbackEngine(llm)
        coord = FeedbackCoordinator(engine, threshold=0.7)

        should_continue, hint = await coord.evaluate("test content")
        assert should_continue is True
        assert hint is not None
        assert "below threshold" in hint

    @pytest.mark.asyncio
    async def test_engine_error_no_retry(self):
        """Should not retry when engine returns ENGINE_ERROR."""
        llm = MagicMock()
        llm.structured_output = AsyncMock(side_effect=Exception("LLM error"))
        engine = FeedbackEngine(llm)
        coord = FeedbackCoordinator(engine, threshold=0.7)

        should_continue, hint = await coord.evaluate("test content")
        assert should_continue is False
        assert hint is None

    @pytest.mark.asyncio
    async def test_validation_error_no_retry(self):
        """Should not retry when engine returns VALIDATION_ERROR."""
        llm = MockLLMClient({"score": 2.0, "issues": ["bad"]})
        engine = FeedbackEngine(llm)
        coord = FeedbackCoordinator(engine, threshold=0.7)

        should_continue, hint = await coord.evaluate("test content")
        assert should_continue is False
        assert hint is None

    @pytest.mark.asyncio
    async def test_no_engine(self):
        """Should return False when no engine is configured."""
        coord = FeedbackCoordinator(None, threshold=0.7)
        should_continue, hint = await coord.evaluate("test content")
        assert should_continue is False
        assert hint is None

    @pytest.mark.asyncio
    async def test_empty_content(self):
        """Should return False when content is empty."""
        llm = MockLLMClient({"score": 0.9, "issues": []})
        engine = FeedbackEngine(llm)
        coord = FeedbackCoordinator(engine, threshold=0.7)

        should_continue, hint = await coord.evaluate("")
        assert should_continue is False
        assert hint is None

    @pytest.mark.asyncio
    async def test_max_retries_reached(self):
        """Should return False when max retries reached."""
        llm = MockLLMClient({"score": 0.4, "issues": ["bad"]})
        engine = FeedbackEngine(llm)
        coord = FeedbackCoordinator(engine, threshold=0.7, max_retries=1)

        # First call triggers retry
        should_continue, hint = await coord.evaluate("test")
        assert should_continue is True

        # Second call hits max retries
        should_continue, hint = await coord.evaluate("test")
        assert should_continue is False
        assert hint is None
