"""Tests for factory-per-case EvalRunner."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vibe.core.query_loop import QueryLoop
from vibe.evals.runner import EvalRunner, QueryLoopFactory
from vibe.harness.memory.eval_store import EvalCase, EvalResult


class MockQueryLoop:
    """Mock QueryLoop for testing."""

    def __init__(self, name="default"):
        self.name = name
        self.messages = []
        self._results = []

    def clear_history(self):
        self.messages = []

    async def run(self, initial_query=""):
        for result in self._results:
            yield result

    async def close(self):
        pass


class TestFactoryPerCase:
    """Test factory-per-case pattern."""

    def test_default_query_loop(self):
        """Should use default query loop when no factory specified."""
        loop = MockQueryLoop("default")
        runner = EvalRunner(query_loop=loop)

        case = EvalCase(
            id="test-1",
            input={"prompt": "hello"},
            expected={},
            tags=[],
        )

        ql = runner._get_query_loop(case)
        assert ql is loop

    def test_default_factory(self):
        """Should use runner's default factory."""
        loop = MockQueryLoop("default")
        custom_loop = MockQueryLoop("custom")

        def factory(case: EvalCase) -> QueryLoop:
            return custom_loop

        runner = EvalRunner(query_loop=loop, default_factory=factory)

        case = EvalCase(
            id="test-1",
            input={"prompt": "hello"},
            expected={},
            tags=[],
        )

        ql = runner._get_query_loop(case)
        assert ql is custom_loop

    def test_case_specific_factory(self):
        """Should use case-specific factory over default."""
        loop = MockQueryLoop("default")
        custom_loop = MockQueryLoop("custom")
        case_loop = MockQueryLoop("case-specific")

        def default_factory(case: EvalCase) -> QueryLoop:
            return custom_loop

        def case_factory(case: EvalCase) -> QueryLoop:
            return case_loop

        runner = EvalRunner(query_loop=loop, default_factory=default_factory)

        case = EvalCase(
            id="test-1",
            input={"prompt": "hello"},
            expected={},
            tags=[],
        )
        # Simulate metadata with factory
        case.metadata = {"query_loop_factory": case_factory}

        ql = runner._get_query_loop(case)
        assert ql is case_loop

    def test_factory_receives_case(self):
        """Factory should receive the case as argument."""
        loop = MockQueryLoop("default")
        received_cases = []

        def factory(case: EvalCase) -> QueryLoop:
            received_cases.append(case)
            return loop

        runner = EvalRunner(query_loop=loop, default_factory=factory)

        case = EvalCase(
            id="test-1",
            input={"prompt": "hello"},
            expected={},
            tags=[],
        )

        runner._get_query_loop(case)
        assert len(received_cases) == 1
        assert received_cases[0].id == "test-1"

    def test_no_factory_no_metadata(self):
        """Should handle case without metadata."""
        loop = MockQueryLoop("default")
        runner = EvalRunner(query_loop=loop)

        case = EvalCase(
            id="test-1",
            input={"prompt": "hello"},
            expected={},
            tags=[],
        )
        # No metadata attribute
        if hasattr(case, "metadata"):
            delattr(case, "metadata")

        ql = runner._get_query_loop(case)
        assert ql is loop


class TestEvalRunnerTypeAlias:
    """Test QueryLoopFactory type alias."""

    def test_type_alias(self):
        """QueryLoopFactory should be callable."""
        def factory(case: EvalCase) -> QueryLoop:
            return MockQueryLoop()

        # Should be callable
        result = factory(EvalCase(id="test", input={}, expected={}, tags=[]))
        assert isinstance(result, MockQueryLoop)
