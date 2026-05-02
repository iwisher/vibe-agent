"""Tests for factory-per-case EvalRunner."""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vibe.core.query_loop import QueryLoop, QueryResult
from vibe.evals.runner import EvalRunner, QueryLoopFactory
from vibe.harness.memory.eval_store import EvalCase, EvalResult


class MockQueryLoop:
    """Mock QueryLoop for testing."""

    def __init__(self, name="default"):
        self.name = name
        self.messages = []
        self._results = []
        self._closed = False

    def clear_history(self):
        self.messages = []

    async def run(self, initial_query=""):
        for result in self._results:
            yield result

    async def close(self):
        self._closed = True

    def copy(self):
        new = MockQueryLoop(self.name)
        new._results = self._results
        return new


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


class TestRunAll:
    """Test run_all behavior: fresh loops, concurrency, cleanup."""

    @pytest.mark.asyncio
    async def test_fresh_query_loop_per_case(self):
        """Each case should receive a distinct QueryLoop instance."""
        loop = MockQueryLoop("default")
        runner = EvalRunner(query_loop=loop)

        cases = [
            EvalCase(id="case-1", input={"prompt": "hello"}, expected={}, tags=[]),
            EvalCase(id="case-2", input={"prompt": "world"}, expected={}, tags=[]),
        ]

        passed_loops = []
        original_run_case = runner.run_case

        async def mock_run_case(case, query_loop=None):
            passed_loops.append(query_loop)
            return EvalResult(eval_id=case.id, passed=True, diff={}, total_tokens=0)

        runner.run_case = mock_run_case

        await runner.run_all(cases)

        assert len(passed_loops) == 2
        assert passed_loops[0] is not passed_loops[1]
        assert passed_loops[0] is not loop
        assert passed_loops[1] is not loop

    @pytest.mark.asyncio
    async def test_concurrent_execution(self):
        """Cases should run concurrently, not sequentially."""
        loop = MockQueryLoop("default")
        runner = EvalRunner(query_loop=loop, max_concurrency=3)

        async def slow_run(initial_query=""):
            await asyncio.sleep(0.1)
            yield QueryResult(response="ok")

        loop.run = slow_run

        cases = [
            EvalCase(id="case-1", input={"prompt": "hello"}, expected={}, tags=[]),
            EvalCase(id="case-2", input={"prompt": "world"}, expected={}, tags=[]),
        ]

        start = time.time()
        results = await runner.run_all(cases)
        elapsed = time.time() - start

        assert len(results) == 2
        # Concurrent execution of two 0.1s sleeps should finish in < 0.18s.
        assert elapsed < 0.18

    @pytest.mark.asyncio
    async def test_all_default_loops_closed(self):
        """Copies of the default QueryLoop should be closed after use."""
        loop = MockQueryLoop("default")
        copied_loops = []

        original_copy = loop.copy

        def tracking_copy():
            new_loop = original_copy()
            copied_loops.append(new_loop)
            return new_loop

        loop.copy = tracking_copy

        runner = EvalRunner(query_loop=loop)

        cases = [
            EvalCase(id="case-1", input={"prompt": "hello"}, expected={}, tags=[]),
            EvalCase(id="case-2", input={"prompt": "world"}, expected={}, tags=[]),
        ]

        await runner.run_all(cases)

        assert len(copied_loops) == 2
        assert all(l._closed for l in copied_loops)
        assert not loop._closed

    @pytest.mark.asyncio
    async def test_factory_loops_closed(self):
        """Factory-created QueryLoops should also be closed after use."""
        loop = MockQueryLoop("default")
        factory_loops = []

        def factory(case):
            new_loop = MockQueryLoop("factory")
            new_loop.copy = lambda: new_loop  # Factory loops don't need copying
            factory_loops.append(new_loop)
            return new_loop

        runner = EvalRunner(query_loop=loop, default_factory=factory)

        cases = [
            EvalCase(id="case-1", input={"prompt": "hello"}, expected={}, tags=[]),
            EvalCase(id="case-2", input={"prompt": "world"}, expected={}, tags=[]),
        ]

        await runner.run_all(cases)

        assert len(factory_loops) == 2
        assert all(l._closed for l in factory_loops)
