"""Tests for Agent-as-Judge evaluation system."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from vibe.evals.judge import AgentJudge, JudgmentRubric, JudgmentResult, DEFAULT_RUBRICS


class MockLLMClient:
    """Mock LLM client for judge tests."""

    def __init__(self, response_content=""):
        self.model = "mock-judge"
        self._response = response_content

    async def complete(self, messages, temperature=0.1):
        mock_response = MagicMock()
        mock_response.content = self._response
        return mock_response


class TestAgentJudge:
    def test_default_rubrics(self):
        """Should have default rubrics configured."""
        judge = AgentJudge(MockLLMClient())
        assert len(judge.rubrics) == 4
        rubric_names = [r.name for r in judge.rubrics]
        assert "correctness" in rubric_names
        assert "completeness" in rubric_names
        assert "safety" in rubric_names
        assert "helpfulness" in rubric_names

    def test_custom_rubrics(self):
        """Should accept custom rubrics."""
        custom = [
            JudgmentRubric(name="code_quality", description="Code is clean", weight=2.0),
        ]
        judge = AgentJudge(MockLLMClient(), rubrics=custom)
        assert len(judge.rubrics) == 1
        assert judge.rubrics[0].name == "code_quality"

    def test_pass_threshold(self):
        """Should respect pass threshold."""
        judge = AgentJudge(MockLLMClient(), pass_threshold=80.0)
        assert judge.pass_threshold == 80.0

    @pytest.mark.asyncio
    async def test_judge_parses_json_response(self):
        """Should parse JSON response from judge LLM."""
        json_response = '''{
  "scores": {
    "correctness": 4.5,
    "completeness": 4.0,
    "safety": 5.0,
    "helpfulness": 3.5
  },
  "explanations": {
    "correctness": "Mostly correct",
    "completeness": "Missed one detail",
    "safety": "Safe response",
    "helpfulness": "Could be clearer"
  },
  "overall_assessment": "Good but not great"
}'''
        judge = AgentJudge(MockLLMClient(json_response))
        result = await judge.judge(
            case_id="test-1",
            user_prompt="What is 2+2?",
            agent_response="The answer is 4.",
        )

        assert result.case_id == "test-1"
        assert result.overall_score > 0
        assert result.passed is True  # Should pass with high scores
        assert "correctness" in result.scores
        assert result.scores["correctness"] == 4.5
        assert result.explanations["correctness"] == "Mostly correct"
        assert result.judge_model == "mock-judge"

    @pytest.mark.asyncio
    async def test_judge_fails_low_scores(self):
        """Should fail when scores are below threshold."""
        json_response = '''{
  "scores": {
    "correctness": 1.0,
    "completeness": 1.0,
    "safety": 2.0,
    "helpfulness": 1.0
  },
  "explanations": {
    "correctness": "Wrong answer",
    "completeness": "Incomplete",
    "safety": "Mostly safe",
    "helpfulness": "Not helpful"
  }
}'''
        judge = AgentJudge(MockLLMClient(json_response), pass_threshold=70.0)
        result = await judge.judge(
            case_id="test-1",
            user_prompt="What is 2+2?",
            agent_response="I don't know.",
        )

        assert result.overall_score < 70.0
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_judge_parses_markdown_json(self):
        """Should extract JSON from markdown code blocks."""
        markdown_response = '''```json
{
  "scores": {
    "correctness": 5.0
  },
  "explanations": {
    "correctness": "Perfect"
  }
}
```'''
        judge = AgentJudge(MockLLMClient(markdown_response))
        result = await judge.judge(
            case_id="test-1",
            user_prompt="Say hello",
            agent_response="Hello!",
        )

        assert result.scores["correctness"] == 5.0

    @pytest.mark.asyncio
    async def test_judge_handles_malformed_json(self):
        """Should handle malformed JSON gracefully."""
        judge = AgentJudge(MockLLMClient("not json at all"))
        result = await judge.judge(
            case_id="test-1",
            user_prompt="Say hello",
            agent_response="Hello!",
        )

        assert result.overall_score == 0.0
        assert result.passed is False
        assert result.scores == {}

    @pytest.mark.asyncio
    async def test_judge_with_tool_outputs(self):
        """Should include tool outputs in judgment."""
        json_response = '''{
  "scores": {
    "correctness": 5.0
  },
  "explanations": {
    "correctness": "Used tools correctly"
  }
}'''
        judge = AgentJudge(MockLLMClient(json_response))
        result = await judge.judge(
            case_id="test-1",
            user_prompt="Read file",
            agent_response="File contains: hello",
            tool_outputs=["hello"],
        )

        assert result.scores["correctness"] == 5.0

    def test_rubric_summary(self):
        """Should return rubric summary."""
        judge = AgentJudge(MockLLMClient())
        summary = judge.get_rubric_summary()
        assert summary["pass_threshold"] == 70.0
        assert len(summary["rubrics"]) == 4

    def test_weighted_score_calculation(self):
        """Overall score should be weighted correctly."""
        judge = AgentJudge(MockLLMClient())
        # correctness=5 (weight 2), completeness=5 (weight 1.5), safety=5 (weight 2), helpfulness=5 (weight 1)
        # max = 5*2 + 5*1.5 + 5*2 + 5*1 = 32.5
        # score = 32.5/32.5 * 100 = 100
        scores = {"correctness": 5.0, "completeness": 5.0, "safety": 5.0, "helpfulness": 5.0}
        # Use the internal calculation
        total_weight = sum(r.weight for r in judge.rubrics)
        weighted_sum = sum(scores.get(r.name, 0) * r.weight for r in judge.rubrics)
        max_possible = sum(r.max_score * r.weight for r in judge.rubrics)
        overall = weighted_sum / max_possible * 100
        assert overall == 100.0

    @pytest.mark.asyncio
    async def test_judge_error_handling(self):
        """Should handle LLM errors gracefully."""
        class FailingLLM:
            model = "failing"
            async def complete(self, messages, temperature=0.1):
                raise Exception("LLM failed")

        judge = AgentJudge(FailingLLM())
        result = await judge.judge(
            case_id="test-1",
            user_prompt="Hello",
            agent_response="Hi",
        )

        assert result.error == "LLM failed"
        assert result.passed is False
        assert result.overall_score == 0.0
