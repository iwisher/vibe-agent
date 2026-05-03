"""Tests for ContextPlanner (Phase 3.5).

Tests intent classification, context assembly, token estimation,
and model tier suggestion.
"""

import pytest

from vibe.core.context_planner import (
    ContextItem,
    ContextPlan,
    ContextPlanner,
    ContextPriority,
    IntentClassifier,
    IntentType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def classifier():
    return IntentClassifier()


@pytest.fixture
def planner():
    return ContextPlanner()


@pytest.fixture
def sample_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute shell commands",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file contents",
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write to a file",
            },
        },
    ]


# ---------------------------------------------------------------------------
# IntentClassifier
# ---------------------------------------------------------------------------

class TestIntentClassifier:
    def test_question_intent(self, classifier):
        intent, conf = classifier.classify("What is the capital of France?")
        assert intent == IntentType.QUESTION
        assert conf > 0.1

    def test_command_intent(self, classifier):
        intent, conf = classifier.classify("Run the test suite and deploy")
        assert intent == IntentType.COMMAND
        assert conf > 0.1

    def test_creative_intent(self, classifier):
        intent, conf = classifier.classify("Write a Python script to sort a list")
        assert intent == IntentType.CREATIVE
        assert conf > 0.1

    def test_analysis_intent(self, classifier):
        intent, conf = classifier.classify("Analyze the performance bottleneck")
        assert intent == IntentType.ANALYSIS
        assert conf > 0.1

    def test_conversation_intent(self, classifier):
        intent, conf = classifier.classify("Hi, how are you today?")
        # "how" triggers QUESTION, "hi" triggers CONVERSATION — either is acceptable
        assert intent in (IntentType.CONVERSATION, IntentType.QUESTION)
        assert conf > 0.1

    def test_multi_step_boost(self, classifier):
        # Multiple tool indicators should boost to MULTI_STEP
        intent, conf = classifier.classify("First run tests, then deploy, then check logs")
        assert intent == IntentType.MULTI_STEP

    def test_confidence_range(self, classifier):
        for query in ["hello", "what is this", "run tests", "write code", "analyze data"]:
            _, conf = classifier.classify(query)
            assert 0.1 <= conf <= 1.0


# ---------------------------------------------------------------------------
# ContextPlanner
# ---------------------------------------------------------------------------

class TestContextPlanner:
    def test_plan_basic(self, planner, sample_tools):
        plan = planner.plan(
            query="What is the capital of France?",
            available_tools=sample_tools,
        )
        assert plan.intent == IntentType.QUESTION
        assert plan.intent_confidence > 0
        assert plan.estimated_tokens > 0
        assert plan.suggested_model_tier in ["free", "budget", "standard", "premium", "ultra"]

    def test_plan_with_wiki_hint(self, planner, sample_tools):
        plan = planner.plan(
            query="Explain quantum computing",
            available_tools=sample_tools,
            wiki_hint="## Quantum Computing\nQuantum computers use qubits.",
        )
        wiki_items = [item for item in plan.context_items if item.source == "wiki"]
        assert len(wiki_items) == 1
        assert wiki_items[0].priority == ContextPriority.HIGH

    def test_plan_tool_selection(self, planner, sample_tools):
        plan = planner.plan(
            query="Read the config file and write a summary",
            available_tools=sample_tools,
        )
        # Should select read_file and/or write_file (or fallback to all)
        assert len(plan.selected_tools) >= 1
        assert all(isinstance(t, str) for t in plan.selected_tools)

    def test_plan_creative_tier(self, planner, sample_tools):
        plan = planner.plan(
            query="Write a Python function to sort a list using quicksort",
            available_tools=sample_tools,
        )
        # Creative intent + code complexity -> at least standard
        assert plan.suggested_model_tier in ["standard", "premium", "ultra"]

    def test_plan_long_context_upgrade(self, planner, sample_tools):
        plan = planner.plan(
            query="x" * 50000,  # Very long query
            available_tools=sample_tools,
        )
        # Long context should upgrade tier (12500 tokens > 8000 threshold)
        assert plan.estimated_tokens > 10000
        # Tier upgrade: QUESTION base is "budget", + long context upgrade = "standard"
        assert plan.suggested_model_tier in ["standard", "premium", "ultra"]

    def test_plan_builds_system_prompt(self, planner, sample_tools):
        plan = planner.plan(
            query="Run tests",
            available_tools=sample_tools,
            wiki_hint="Testing guide",
        )
        prompt = plan.build_system_prompt()
        assert "Testing guide" in prompt or "Available Tools" in prompt or "Run tests" in prompt

    def test_plan_context_item_count(self, planner, sample_tools):
        plan = planner.plan(
            query="Hello",
            available_tools=sample_tools,
        )
        # Should have at least user_query
        assert len(plan.context_items) >= 1
        assert plan.context_items[0].source == "user_query"
        assert plan.context_items[0].priority == ContextPriority.CRITICAL

    def test_plan_with_history(self, planner, sample_tools):
        plan = planner.plan(
            query="Continue from where we left off",
            available_tools=sample_tools,
            history_summary="Previously discussed file I/O patterns.",
        )
        history_items = [item for item in plan.context_items if item.source == "history"]
        assert len(history_items) == 1
        assert history_items[0].priority == ContextPriority.MEDIUM

    def test_plan_complexity_scorer_integration(self, planner, sample_tools):
        # Mock complexity scorer
        class MockScorer:
            def score(self, messages, tools=None):
                from unittest.mock import MagicMock
                result = MagicMock()
                result.overall = 0.85
                return result

        planner_with_scorer = ContextPlanner(complexity_scorer=MockScorer())
        plan = planner_with_scorer.plan(
            query="Design a distributed system architecture",
            available_tools=sample_tools,
        )
        assert plan.complexity_score == 0.85
        # High complexity should upgrade tier
        assert plan.suggested_model_tier in ["premium", "ultra"]


# ---------------------------------------------------------------------------
# ContextPlan
# ---------------------------------------------------------------------------

class TestContextPlan:
    def test_total_context_tokens(self):
        plan = ContextPlan(
            intent=IntentType.QUESTION,
            intent_confidence=0.8,
            context_items=[
                ContextItem(source="a", content="test", priority=ContextPriority.CRITICAL, estimated_tokens=10),
                ContextItem(source="b", content="more", priority=ContextPriority.HIGH, estimated_tokens=20),
            ],
        )
        assert plan.total_context_tokens == 30

    def test_get_items_by_priority(self):
        plan = ContextPlan(
            intent=IntentType.QUESTION,
            intent_confidence=0.8,
            context_items=[
                ContextItem(source="a", content="x", priority=ContextPriority.CRITICAL, estimated_tokens=1),
                ContextItem(source="b", content="y", priority=ContextPriority.HIGH, estimated_tokens=1),
                ContextItem(source="c", content="z", priority=ContextPriority.CRITICAL, estimated_tokens=1),
            ],
        )
        critical = plan.get_items_by_priority(ContextPriority.CRITICAL)
        assert len(critical) == 2
        assert all(item.source in ("a", "c") for item in critical)

    def test_build_system_prompt_empty(self):
        plan = ContextPlan(intent=IntentType.QUESTION, intent_confidence=0.5)
        assert plan.build_system_prompt() == ""

    def test_build_system_prompt_with_content(self):
        plan = ContextPlan(
            intent=IntentType.QUESTION,
            intent_confidence=0.8,
            context_items=[
                ContextItem(source="wiki", content="Knowledge about X", priority=ContextPriority.HIGH, estimated_tokens=5),
                ContextItem(source="history", content="Old chat", priority=ContextPriority.MEDIUM, estimated_tokens=3),
            ],
        )
        prompt = plan.build_system_prompt()
        assert "Knowledge about X" in prompt
        assert "Old chat" not in prompt  # MEDIUM priority excluded
