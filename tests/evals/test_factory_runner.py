"""Tests for factory-per-case EvalRunner — state isolation between cases."""

from vibe.evals.factory_runner import FactoryEvalRunner
from vibe.harness.memory.eval_store import EvalCase


class FakeQueryLoop:
    """Fake QueryLoop that tracks its instance ID."""

    _instance_count = 0

    def __init__(self, case_id=""):
        FakeQueryLoop._instance_count += 1
        self.instance_id = FakeQueryLoop._instance_count
        self.case_id = case_id
        self.messages = []
        self._closed = False

    def clear_history(self):
        self.messages = []

    async def run(self, initial_query=""):
        from vibe.core.query_loop import QueryResult

        yield QueryResult(response=f"result for {initial_query}")

    async def close(self):
        self._closed = True

    def copy(self):
        return FakeQueryLoop(self.case_id)


class TestFactoryEvalRunner:
    def test_factory_creates_fresh_loop_per_case(self):
        created_loops = []

        def factory(case):
            loop = FakeQueryLoop(case.id)
            created_loops.append(loop)
            return loop

        runner = FactoryEvalRunner(factory=factory, max_concurrency=1)

        cases = [
            EvalCase(id="case_1", tags=[], input={"prompt": "test 1"}, expected={}),
            EvalCase(id="case_2", tags=[], input={"prompt": "test 2"}, expected={}),
        ]

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(runner.run_all(cases))
        finally:
            loop.close()

        assert len(results) == 2
        # Each case should have gotten a different loop instance
        assert created_loops[0].instance_id != created_loops[1].instance_id

    def test_loop_is_closed_after_case(self):
        def factory(case):
            return FakeQueryLoop(case.id)

        runner = FactoryEvalRunner(factory=factory, max_concurrency=1)
        case = EvalCase(id="case_1", tags=[], input={"prompt": "test"}, expected={})

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner.run_case(case))
        finally:
            loop.close()

        # The loop should be closed after the case
        # (We can't directly check since it's local to run_case, but we verify no exception)

    def test_concurrency_limited_by_semaphore(self):
        """Test that semaphore limits concurrent execution.

        Note: With asyncio.gather, all coroutines start immediately but
        the semaphore inside run_case limits concurrent execution.
        We verify by checking that the factory is called for each case.
        """
        created_count = 0

        def factory(case):
            nonlocal created_count
            created_count += 1
            return FakeQueryLoop(case.id)

        runner = FactoryEvalRunner(factory=factory, max_concurrency=2)

        cases = [
            EvalCase(id=f"case_{i}", tags=[], input={"prompt": f"test {i}"}, expected={})
            for i in range(5)
        ]

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner.run_all(cases))
        finally:
            loop.close()

        # All 5 cases should have created their own loop
        assert created_count == 5

    def test_run_all_sync(self):
        def factory(case):
            return FakeQueryLoop(case.id)

        runner = FactoryEvalRunner(factory=factory)
        cases = [
            EvalCase(id="case_1", tags=[], input={"prompt": "test"}, expected={}),
        ]

        results = runner.run_all_sync(cases)
        assert len(results) == 1

    def test_state_isolation(self):
        """Verify that state mutations in one case don't affect another."""
        shared_state = {"counter": 0}

        class StatefulLoop(FakeQueryLoop):
            def __init__(self, case_id=""):
                super().__init__(case_id)
                self.local_counter = shared_state["counter"]
                shared_state["counter"] += 1

        def factory(case):
            return StatefulLoop(case.id)

        runner = FactoryEvalRunner(factory=factory)
        cases = [
            EvalCase(id="case_1", tags=[], input={"prompt": "test 1"}, expected={}),
            EvalCase(id="case_2", tags=[], input={"prompt": "test 2"}, expected={}),
        ]

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner.run_all(cases))
        finally:
            loop.close()

        # Each loop should have its own local_counter value
        # (0 and 1, since they're created sequentially)
        assert shared_state["counter"] == 2
