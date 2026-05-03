"""Multi-turn Conversation State Machine for Vibe Agent.

Manages conversation flow through explicit states:
- IDLE: Waiting for user input
- PLANNING: Selecting tools/skills/MCPs
- AWAITING_USER_INPUT: Waiting for clarification or follow-up
- TOOL_EXECUTING: Running tools
- SYNTHESIZING: Processing results
- COMPLETED: Conversation finished
- ERROR: Error state with recovery options

Supports:
- State transitions with validation
- Timeout handling per state
- Interrupt handling (user can stop at any point)
- Conversation branching (fork/merge for parallel tool execution)
"""

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional


class ConversationState(Enum):
    """States in the conversation lifecycle."""
    IDLE = auto()
    PLANNING = auto()
    AWAITING_USER_INPUT = auto()  # Waiting for clarification/confirmation
    TOOL_EXECUTING = auto()
    SYNTHESIZING = auto()
    COMPLETED = auto()
    INCOMPLETE = auto()  # Max iterations reached
    ERROR = auto()
    STOPPED = auto()


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


@dataclass
class StateTransition:
    """Record of a state transition."""
    from_state: ConversationState
    to_state: ConversationState
    timestamp: float
    reason: str = ""


@dataclass
class ConversationBranch:
    """A branch in the conversation for parallel execution."""
    branch_id: str
    parent_state: ConversationState
    created_at: float
    merged_at: Optional[float] = None
    results: list[Any] = field(default_factory=list)


class ConversationStateMachine:
    """Explicit state machine for multi-turn conversations.

    Features:
    - Validated state transitions (no illegal jumps)
    - Timeout tracking per state
    - Transition history for debugging
    - Branch support for parallel tool execution
    - Interrupt handling
    """

    # Valid transitions: from_state -> [allowed_to_states]
    VALID_TRANSITIONS = {
        ConversationState.IDLE: [
            ConversationState.PLANNING,
            ConversationState.STOPPED,
        ],
        ConversationState.PLANNING: [
            ConversationState.TOOL_EXECUTING,
            ConversationState.AWAITING_USER_INPUT,
            ConversationState.SYNTHESIZING,  # No tools needed
            ConversationState.COMPLETED,  # Direct answer, no tools needed
            ConversationState.ERROR,
            ConversationState.STOPPED,
        ],
        ConversationState.AWAITING_USER_INPUT: [
            ConversationState.PLANNING,
            ConversationState.TOOL_EXECUTING,
            ConversationState.STOPPED,
        ],
        ConversationState.TOOL_EXECUTING: [
            ConversationState.SYNTHESIZING,
            ConversationState.ERROR,
            ConversationState.STOPPED,
        ],
        ConversationState.SYNTHESIZING: [
            ConversationState.COMPLETED,
            ConversationState.PLANNING,  # Multi-turn: continue
            ConversationState.AWAITING_USER_INPUT,
            ConversationState.INCOMPLETE,
            ConversationState.ERROR,
            ConversationState.STOPPED,
        ],
        ConversationState.COMPLETED: [
            ConversationState.IDLE,  # New conversation
            ConversationState.PLANNING,  # Continue with follow-up
            ConversationState.STOPPED,
        ],
        ConversationState.INCOMPLETE: [
            ConversationState.IDLE,
            ConversationState.PLANNING,
            ConversationState.STOPPED,
        ],
        ConversationState.ERROR: [
            ConversationState.PLANNING,  # Retry
            ConversationState.IDLE,
            ConversationState.STOPPED,
        ],
        ConversationState.STOPPED: [
            ConversationState.IDLE,  # Restart
        ],
    }

    # Default timeouts per state (seconds)
    DEFAULT_TIMEOUTS = {
        ConversationState.PLANNING: 30.0,
        ConversationState.TOOL_EXECUTING: 120.0,
        ConversationState.SYNTHESIZING: 60.0,
        ConversationState.AWAITING_USER_INPUT: 300.0,  # 5 min for user
    }
    def __init__(
        self,
        initial_state: ConversationState = ConversationState.IDLE,
        timeouts: Optional[dict[ConversationState, float]] = None,
        on_transition: Optional[Callable[[StateTransition], None]] = None,
    ):
        """Initialize state machine with optional starting state.

        .. deprecated::
            ConversationStateMachine is deprecated and will be removed in v2.0.
            QueryLoop now uses its own QueryState enum.
        """
        import warnings
        warnings.warn(
            "ConversationStateMachine is deprecated and will be removed in v2.0. "
            "QueryLoop now uses its own QueryState enum.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._state = ConversationState.IDLE
        self._transition_history: list[StateTransition] = []
        self._state_entry_time: float = time.time()
        self._timeouts = timeouts or dict(self.DEFAULT_TIMEOUTS)
        self._on_transition = on_transition
        self._branches: dict[str, ConversationBranch] = {}
        self._current_branch: Optional[str] = None
        self._interrupt_requested = False

    @property
    def state(self) -> ConversationState:
        return self._state

    @property
    def transition_history(self) -> list[StateTransition]:
        return list(self._transition_history)

    def can_transition(self, to_state: ConversationState) -> bool:
        """Check if a transition to the given state is valid."""
        allowed = self.VALID_TRANSITIONS.get(self._state, [])
        return to_state in allowed

    def transition(self, to_state: ConversationState, reason: str = "") -> StateTransition:
        """Transition to a new state.

        Args:
            to_state: Target state
            reason: Reason for the transition

        Returns:
            StateTransition record

        Raises:
            StateTransitionError: If the transition is invalid
        """
        if not self.can_transition(to_state):
            raise StateTransitionError(
                f"Invalid transition: {self._state.name} -> {to_state.name}"
            )

        transition = StateTransition(
            from_state=self._state,
            to_state=to_state,
            timestamp=time.time(),
            reason=reason,
        )
        self._transition_history.append(transition)
        self._state = to_state
        self._state_entry_time = time.time()

        if self._on_transition:
            self._on_transition(transition)

        return transition

    def transition_if_valid(self, to_state: ConversationState, reason: str = "") -> Optional[StateTransition]:
        """Transition only if valid, otherwise return None."""
        if self.can_transition(to_state):
            return self.transition(to_state, reason)
        return None

    def is_timeout(self) -> bool:
        """Check if the current state has timed out."""
        timeout = self._timeouts.get(self._state)
        if timeout is None:
            return False
        elapsed = time.time() - self._state_entry_time
        return elapsed > timeout

    def time_in_state(self) -> float:
        """Return seconds spent in current state."""
        return time.time() - self._state_entry_time

    def request_interrupt(self) -> None:
        """Request an interrupt (e.g., user pressed stop)."""
        self._interrupt_requested = True

    def clear_interrupt(self) -> None:
        """Clear the interrupt flag."""
        self._interrupt_requested = False

    @property
    def is_interrupted(self) -> bool:
        return self._interrupt_requested

    def create_branch(self, branch_id: str) -> ConversationBranch:
        """Create a new conversation branch for parallel execution.

        Args:
            branch_id: Unique identifier for the branch

        Returns:
            ConversationBranch
        """
        branch = ConversationBranch(
            branch_id=branch_id,
            parent_state=self._state,
            created_at=time.time(),
        )
        self._branches[branch_id] = branch
        return branch

    def merge_branch(self, branch_id: str, result: Any) -> None:
        """Merge a branch back into the main conversation.

        Args:
            branch_id: Branch to merge
            result: Result from the branch execution
        """
        branch = self._branches.get(branch_id)
        if branch:
            branch.merged_at = time.time()
            branch.results.append(result)

    def get_branch_results(self, branch_id: str) -> list[Any]:
        """Get results from a merged branch."""
        branch = self._branches.get(branch_id)
        return branch.results if branch else []

    def reset(self) -> None:
        """Reset to initial state."""
        self._state = ConversationState.IDLE
        self._transition_history.clear()
        self._state_entry_time = time.time()
        self._branches.clear()
        self._current_branch = None
        self._interrupt_requested = False

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the conversation state."""
        return {
            "current_state": self._state.name,
            "time_in_state_seconds": round(self.time_in_state(), 2),
            "is_timeout": self.is_timeout(),
            "is_interrupted": self._interrupt_requested,
            "transition_count": len(self._transition_history),
            "branches": len(self._branches),
            "last_transition": (
                {
                    "from": self._transition_history[-1].from_state.name,
                    "to": self._transition_history[-1].to_state.name,
                    "reason": self._transition_history[-1].reason,
                }
                if self._transition_history
                else None
            ),
        }
