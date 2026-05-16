"""Adaptive iteration budget for QueryLoop.

Replaces the hard max_iterations=50 limit with a dynamic budget system that:
1. Allocates budget based on query complexity (simple vs multi-step)
2. Tracks token burn rate and adjusts remaining budget
3. Provides early-exit heuristics (completion signals, stagnation detection)
4. Respects absolute safety caps to prevent runaway loops
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class BudgetSignal(Enum):
    """Signals that can modify or terminate the iteration budget."""

    CONTINUE = auto()
    COMPLETION_DETECTED = auto()  # LLM emitted a completion phrase
    STAGNATION = auto()           # No progress for N iterations
    TOKEN_BURN_HIGH = auto()      # Approaching context limit
    TOOL_CHAIN_LONG = auto()      # Excessive tool chaining
    USER_STOP = auto()            # External stop signal


@dataclass
class BudgetConfig:
    """Configuration for adaptive budgeting."""

    # Base allocation
    min_iterations: int = 3           # Absolute minimum (simple queries)
    max_iterations: int = 50          # Absolute safety cap
    default_budget: int = 15          # Starting point for most queries

    # Complexity multipliers
    multi_step_multiplier: float = 2.0    # Budget ×2 for multi-step queries
    tool_heavy_multiplier: float = 1.5    # Budget ×1.5 for tool-heavy queries
    reasoning_multiplier: float = 1.8     # Budget ×1.8 for reasoning tasks

    # Early-exit thresholds
    stagnation_window: int = 4        # Iterations without progress to trigger stagnation
    completion_phrases: list[str] = field(default_factory=lambda: [
        "task complete", "done", "finished", "completed successfully",
        "that's all", "no further action", "all done",
    ])

    # Token burn awareness
    token_budget_ratio: float = 0.8   # Reduce iterations when context is 80% full

    # Tool chain limits
    max_consecutive_tools: int = 8    # Max tool calls in a row before forced synthesis

    @classmethod
    def from_config(cls, config: Any | None) -> "BudgetConfig":
        """Build from VibeConfig query_loop section."""
        if config is None:
            return cls()
        ql = getattr(config, "query_loop", None)
        if ql is None:
            return cls()
        return cls(
            min_iterations=getattr(ql, "min_iterations", 3),
            max_iterations=getattr(ql, "max_iterations", 50),
            default_budget=getattr(ql, "default_budget", 15),
            multi_step_multiplier=getattr(ql, "multi_step_multiplier", 2.0),
            tool_heavy_multiplier=getattr(ql, "tool_heavy_multiplier", 1.5),
            reasoning_multiplier=getattr(ql, "reasoning_multiplier", 1.8),
            stagnation_window=getattr(ql, "stagnation_window", 4),
            token_budget_ratio=getattr(ql, "token_budget_ratio", 0.8),
            max_consecutive_tools=getattr(ql, "max_consecutive_tools", 8),
        )


@dataclass
class IterationBudget:
    """Mutable iteration budget tracker."""

    allocated: int
    consumed: int = 0
    signals: list[BudgetSignal] = field(default_factory=list)
    _stagnation_counter: int = 0
    _last_tool_count: int = 0
    _last_message_count: int = 0
    _consecutive_tools: int = 0
    _start_time: float = field(default_factory=time.time)

    @property
    def remaining(self) -> int:
        return max(0, self.allocated - self.consumed)

    @property
    def exhausted(self) -> bool:
        return self.consumed >= self.allocated

    @property
    def should_exit(self) -> bool:
        """True if any exit signal has been triggered."""
        return any(
            s in (BudgetSignal.COMPLETION_DETECTED, BudgetSignal.USER_STOP)
            for s in self.signals
        )

    def consume(self, n: int = 1) -> None:
        self.consumed += n

    def add_signal(self, signal: BudgetSignal) -> None:
        self.signals.append(signal)

    def check_stagnation(self, current_tools: int, current_messages: int) -> BudgetSignal:
        """Detect if loop is making no progress."""
        if current_tools == self._last_tool_count and current_messages == self._last_message_count:
            self._stagnation_counter += 1
        else:
            self._stagnation_counter = 0
        self._last_tool_count = current_tools
        self._last_message_count = current_messages

        if self._stagnation_counter >= 4:  # Fixed stagnation window
            return BudgetSignal.STAGNATION
        return BudgetSignal.CONTINUE

    def check_completion_phrase(self, text: str) -> BudgetSignal:
        """Detect completion phrases in LLM output."""
        text_lower = text.lower()
        for phrase in ["task complete", "done", "finished", "completed successfully",
                       "that's all", "no further action", "all done", "complete"]:
            if phrase in text_lower:
                return BudgetSignal.COMPLETION_DETECTED
        return BudgetSignal.CONTINUE

    def check_token_pressure(self, current_tokens: int, max_tokens: int) -> BudgetSignal:
        """Detect when approaching context limit."""
        if max_tokens > 0 and current_tokens / max_tokens > 0.8:
            return BudgetSignal.TOKEN_BURN_HIGH
        return BudgetSignal.CONTINUE

    def check_tool_chain(self, had_tool_call: bool) -> BudgetSignal:
        """Detect excessive consecutive tool calls."""
        if had_tool_call:
            self._consecutive_tools += 1
        else:
            self._consecutive_tools = 0

        if self._consecutive_tools >= 8:  # Fixed max consecutive
            return BudgetSignal.TOOL_CHAIN_LONG
        return BudgetSignal.CONTINUE

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocated": self.allocated,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "exhausted": self.exhausted,
            "should_exit": self.should_exit,
            "signals": [s.name for s in self.signals],
            "elapsed_seconds": time.time() - self._start_time,
        }


class AdaptiveBudgetAllocator:
    """Allocates iteration budget based on query characteristics."""

    def __init__(self, config: BudgetConfig | None = None):
        self.config = config or BudgetConfig()

    def allocate(self, query: str, available_tools: list[dict] | None = None) -> IterationBudget:
        """Allocate budget based on query complexity heuristics."""
        budget = self.config.default_budget
        query_lower = query.lower()

        # Multi-step detection
        multi_step_markers = [
            "step by step", "plan", "design", "architecture",
            "compare", "evaluate", "analyze", "debug", "refactor",
            "implement", "create", "build", "setup", "configure",
            "first", "then", "next", "after", "finally",
        ]
        if any(m in query_lower for m in multi_step_markers):
            budget = int(budget * self.config.multi_step_multiplier)

        # Tool-heavy detection
        tool_count = len(available_tools) if available_tools else 0
        if tool_count > 5:
            budget = int(budget * self.config.tool_heavy_multiplier)

        # Reasoning detection
        reasoning_markers = [
            "explain", "why", "how does", "what if", "reason",
            "think through", "walk me through", "deep dive",
        ]
        if any(m in query_lower for m in reasoning_markers):
            budget = int(budget * self.config.reasoning_multiplier)

        # Clamp to safety bounds
        budget = max(self.config.min_iterations, min(budget, self.config.max_iterations))

        return IterationBudget(allocated=budget)
