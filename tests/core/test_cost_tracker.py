"""Tests for cost tracking and spend limits."""

from vibe.core.cost_tracker import CostBudget, CostTracker


class TestCostBudget:
    def test_default_values(self):
        budget = CostBudget()
        assert budget.session_limit is None
        assert budget.daily_limit is None
        assert budget.global_limit is None
        assert budget.warning_threshold == 0.8

    def test_from_config_none(self):
        budget = CostBudget.from_config(None)
        assert budget.session_limit is None

    def test_from_config_mock(self):
        class MockCR:
            session_spend_limit = 10.0
            daily_spend_limit = 50.0
            warning_threshold = 0.75

        class MockConfig:
            cost_router = MockCR()

        budget = CostBudget.from_config(MockConfig())
        assert budget.session_limit == 10.0
        assert budget.daily_limit == 50.0
        assert budget.warning_threshold == 0.75


class TestCostTracker:
    def test_record_and_snapshot(self):
        tracker = CostTracker()
        snapshot = tracker.record(
            provider="openai",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost=0.01,
            session_id="sess-1",
        )
        assert snapshot.session_cost == 0.01
        assert snapshot.limit_exceeded is False

    def test_session_limit_enforcement(self):
        budget = CostBudget(session_limit=0.05)
        tracker = CostTracker(budget=budget)

        # Record 4 calls at 0.01 each = 0.04 (under limit)
        for _ in range(4):
            snapshot = tracker.record(
                provider="openai",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost=0.01,
                session_id="sess-1",
            )
        assert snapshot.limit_exceeded is False

        # 5th call = 0.05 (at limit)
        snapshot = tracker.record(
            provider="openai",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost=0.01,
            session_id="sess-1",
        )
        assert snapshot.limit_exceeded is True

    def test_check_budget_prevents_exceeding(self):
        budget = CostBudget(session_limit=0.05)
        tracker = CostTracker(budget=budget)

        # Spend up to limit
        for _ in range(5):
            tracker.record(
                provider="openai",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost=0.01,
                session_id="sess-1",
            )

        # Next call should be blocked
        assert tracker.check_budget("sess-1", estimated_cost=0.01) is False

    def test_check_budget_allows_under_limit(self):
        budget = CostBudget(session_limit=1.0)
        tracker = CostTracker(budget=budget)

        tracker.record(
            provider="openai",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost=0.01,
            session_id="sess-1",
        )

        assert tracker.check_budget("sess-1", estimated_cost=0.01) is True

    def test_warning_threshold(self):
        budget = CostBudget(session_limit=1.0, warning_threshold=0.5)
        tracker = CostTracker(budget=budget)

        # Spend 60% of limit
        for _ in range(6):
            snapshot = tracker.record(
                provider="openai",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost=0.1,
                session_id="sess-1",
            )
        assert snapshot.warning_triggered is True
        assert snapshot.limit_exceeded is False

    def test_daily_limit(self):
        budget = CostBudget(daily_limit=0.05)
        tracker = CostTracker(budget=budget)

        for _ in range(5):
            snapshot = tracker.record(
                provider="openai",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost=0.01,
                session_id="sess-1",
            )
        assert snapshot.limit_exceeded is True

    def test_global_limit(self):
        budget = CostBudget(global_limit=0.05)
        tracker = CostTracker(budget=budget)

        for _ in range(5):
            snapshot = tracker.record(
                provider="openai",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost=0.01,
                session_id="sess-1",
            )
        assert snapshot.limit_exceeded is True

    def test_get_stats(self):
        tracker = CostTracker()
        tracker.record(
            provider="openai",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost=0.01,
            session_id="sess-1",
        )
        tracker.record(
            provider="anthropic",
            model="claude",
            prompt_tokens=200,
            completion_tokens=100,
            estimated_cost=0.02,
            session_id="sess-1",
        )

        stats = tracker.get_stats()
        assert stats["total_calls"] == 2
        assert stats["total_cost"] == 0.03
        assert stats["total_tokens"] == 450
        assert stats["provider_breakdown"]["openai"] == 0.01
        assert stats["provider_breakdown"]["anthropic"] == 0.02

    def test_multiple_sessions(self):
        tracker = CostTracker()
        tracker.record(
            provider="openai",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost=0.01,
            session_id="sess-1",
        )
        tracker.record(
            provider="openai",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost=0.01,
            session_id="sess-2",
        )

        stats = tracker.get_stats()
        assert stats["session_costs"]["sess-1"] == 0.01
        assert stats["session_costs"]["sess-2"] == 0.01
