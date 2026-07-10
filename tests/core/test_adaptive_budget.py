"""Tests for adaptive iteration budgets."""

from vibe.core.adaptive_budget import (
    AdaptiveBudgetAllocator,
    BudgetConfig,
    BudgetSignal,
    IterationBudget,
)


class TestBudgetConfig:
    def test_default_values(self):
        cfg = BudgetConfig()
        assert cfg.min_iterations == 3
        assert cfg.max_iterations == 50
        assert cfg.default_budget == 15
        assert cfg.token_budget_ratio == 0.8

    def test_from_config_none(self):
        cfg = BudgetConfig.from_config(None)
        assert cfg.default_budget == 15

    def test_from_config_mock(self):
        class MockQL:
            min_iterations = 5
            max_iterations = 100
            default_budget = 20

        class MockConfig:
            query_loop = MockQL()

        cfg = BudgetConfig.from_config(MockConfig())
        assert cfg.min_iterations == 5
        assert cfg.max_iterations == 100
        assert cfg.default_budget == 20


class TestIterationBudget:
    def test_basic_tracking(self):
        budget = IterationBudget(allocated=10)
        assert budget.remaining == 10
        assert not budget.exhausted
        budget.consume(3)
        assert budget.remaining == 7
        assert budget.consumed == 3

    def test_exhaustion(self):
        budget = IterationBudget(allocated=2)
        budget.consume(2)
        assert budget.exhausted
        assert budget.remaining == 0

    def test_should_exit_signals(self):
        budget = IterationBudget(allocated=10)
        assert not budget.should_exit
        budget.add_signal(BudgetSignal.COMPLETION_DETECTED)
        assert budget.should_exit

    def test_stagnation_detection(self):
        budget = IterationBudget(allocated=10)
        # No change for 5 iterations (counter starts at 0, need 5 calls to reach >= 4)
        for _ in range(5):
            sig = budget.check_stagnation(current_tools=0, current_messages=2)
        assert sig == BudgetSignal.STAGNATION

    def test_progress_resets_stagnation(self):
        budget = IterationBudget(allocated=10)
        budget.check_stagnation(current_tools=0, current_messages=2)
        budget.check_stagnation(current_tools=0, current_messages=2)
        sig = budget.check_stagnation(current_tools=1, current_messages=3)
        assert sig == BudgetSignal.CONTINUE
        # Counter reset
        assert budget._stagnation_counter == 0

    def test_completion_phrase_detection(self):
        budget = IterationBudget(allocated=10)
        sig = budget.check_completion_phrase("The task is complete.")
        assert sig == BudgetSignal.COMPLETION_DETECTED

    def test_no_completion_phrase(self):
        budget = IterationBudget(allocated=10)
        sig = budget.check_completion_phrase("Here is the result.")
        assert sig == BudgetSignal.CONTINUE

    def test_token_pressure(self):
        budget = IterationBudget(allocated=10)
        sig = budget.check_token_pressure(current_tokens=8500, max_tokens=10000)
        assert sig == BudgetSignal.TOKEN_BURN_HIGH

    def test_no_token_pressure(self):
        budget = IterationBudget(allocated=10)
        sig = budget.check_token_pressure(current_tokens=4000, max_tokens=10000)
        assert sig == BudgetSignal.CONTINUE

    def test_tool_chain_limit(self):
        budget = IterationBudget(allocated=10)
        for _ in range(8):
            sig = budget.check_tool_chain(had_tool_call=True)
        assert sig == BudgetSignal.TOOL_CHAIN_LONG

    def test_tool_chain_reset(self):
        budget = IterationBudget(allocated=10)
        for _ in range(5):
            budget.check_tool_chain(had_tool_call=True)
        sig = budget.check_tool_chain(had_tool_call=False)
        assert sig == BudgetSignal.CONTINUE
        assert budget._consecutive_tools == 0

    def test_to_dict(self):
        budget = IterationBudget(allocated=10)
        budget.consume(3)
        budget.add_signal(BudgetSignal.COMPLETION_DETECTED)
        d = budget.to_dict()
        assert d["allocated"] == 10
        assert d["consumed"] == 3
        assert d["remaining"] == 7
        assert d["should_exit"] is True
        assert "COMPLETION_DETECTED" in d["signals"]


class TestAdaptiveBudgetAllocator:
    def test_simple_query(self):
        allocator = AdaptiveBudgetAllocator()
        budget = allocator.allocate("Hello world")
        assert budget.allocated == 15  # default

    def test_multi_step_query(self):
        allocator = AdaptiveBudgetAllocator()
        budget = allocator.allocate("Step by step, plan and implement a REST API")
        assert budget.allocated == 30  # 15 * 2.0

    def test_tool_heavy_query(self):
        allocator = AdaptiveBudgetAllocator()
        tools = [{"name": f"tool_{i}"} for i in range(10)]
        budget = allocator.allocate("Run analysis", available_tools=tools)
        assert budget.allocated == 22  # 15 * 1.5, clamped

    def test_reasoning_query(self):
        allocator = AdaptiveBudgetAllocator()
        budget = allocator.allocate("Explain why the sky is blue")
        assert budget.allocated == 27  # 15 * 1.8, clamped to 27

    def test_safety_cap(self):
        allocator = AdaptiveBudgetAllocator(
            BudgetConfig(max_iterations=20, multi_step_multiplier=10.0)
        )
        budget = allocator.allocate("Step by step plan")
        assert budget.allocated == 20  # clamped to max

    def test_safety_floor(self):
        allocator = AdaptiveBudgetAllocator(BudgetConfig(min_iterations=5, default_budget=3))
        budget = allocator.allocate("Hi")
        assert budget.allocated == 5  # clamped to min

    def test_complexity_combination(self):
        """Multi-step + reasoning should multiply but stay within bounds."""
        allocator = AdaptiveBudgetAllocator()
        budget = allocator.allocate("Step by step, explain the architecture and design a system")
        # Both multi_step and reasoning apply: 15 * 2.0 * 1.8 = 54, clamped to 50
        assert budget.allocated == 50
