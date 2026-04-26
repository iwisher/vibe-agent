"""Tests for Conversation State Machine."""
import pytest
from vibe.harness.conversation_state import (
    ConversationState,
    ConversationStateMachine,
    StateTransitionError,
)


class TestConversationStateMachine:
    """Test ConversationStateMachine with deprecation warnings."""

    @pytest.fixture
    def sm(self):
        """Fixture that creates state machine with expected warning."""
        with pytest.warns(DeprecationWarning, match="deprecated and will be removed"):
            return ConversationStateMachine()

    def test_conversation_state_machine_deprecation(self):
        """Verify deprecation warning is raised."""
        with pytest.warns(DeprecationWarning, match="deprecated and will be removed"):
            ConversationStateMachine()

    def test_initial_state(self, sm):
        """Should start in IDLE state."""
        assert sm.state == ConversationState.IDLE

    def test_valid_transition_idle_to_planning(self, sm):
        sm.transition(ConversationState.PLANNING, "Starting")
        assert sm.state == ConversationState.PLANNING

    def test_valid_transition_planning_to_tool_executing(self, sm):
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.TOOL_EXECUTING)
        assert sm.state == ConversationState.TOOL_EXECUTING

    def test_valid_transition_synthesizing_to_completed(self, sm):
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.TOOL_EXECUTING)
        sm.transition(ConversationState.SYNTHESIZING)
        sm.transition(ConversationState.COMPLETED)
        assert sm.state == ConversationState.COMPLETED

    def test_invalid_transition_raises(self, sm):
        with pytest.raises(StateTransitionError):
            sm.transition(ConversationState.TOOL_EXECUTING)

    def test_invalid_idle_to_synthesizing(self, sm):
        with pytest.raises(StateTransitionError):
            sm.transition(ConversationState.SYNTHESIZING)

    def test_can_transition_check(self, sm):
        assert sm.can_transition(ConversationState.PLANNING) is True
        assert sm.can_transition(ConversationState.TOOL_EXECUTING) is False

    def test_transition_if_valid(self, sm):
        result = sm.transition_if_valid(ConversationState.PLANNING)
        assert result is not None
        assert sm.state == ConversationState.PLANNING

    def test_transition_if_valid_invalid(self, sm):
        result = sm.transition_if_valid(ConversationState.COMPLETED)
        assert result is None
        assert sm.state == ConversationState.IDLE

    def test_transition_history(self, sm):
        sm.transition(ConversationState.PLANNING, "Start")
        sm.transition(ConversationState.TOOL_EXECUTING, "Tools needed")
        history = sm.transition_history
        assert len(history) == 2
        assert history[0].from_state == ConversationState.IDLE
        assert history[0].to_state == ConversationState.PLANNING
        assert history[0].reason == "Start"

    def test_multi_turn_conversation(self, sm):
        """Simulate a multi-turn conversation with tool execution."""
        # Turn 1: User asks something, needs tools
        sm.transition(ConversationState.PLANNING, "User query")
        sm.transition(ConversationState.TOOL_EXECUTING, "Need file read")
        sm.transition(ConversationState.SYNTHESIZING, "Processing results")
        sm.transition(ConversationState.COMPLETED, "Answer ready")

        # Turn 2: User follow-up
        sm.transition(ConversationState.PLANNING, "Follow-up query")
        sm.transition(ConversationState.SYNTHESIZING, "No tools needed")
        sm.transition(ConversationState.COMPLETED, "Answer ready")

        assert sm.state == ConversationState.COMPLETED
        assert len(sm.transition_history) == 7

    def test_awaiting_user_input_flow(self, sm):
        """Test clarification flow."""
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.AWAITING_USER_INPUT, "Need clarification")
        sm.transition(ConversationState.PLANNING, "User responded")
        sm.transition(ConversationState.COMPLETED)
        assert sm.state == ConversationState.COMPLETED

    def test_error_recovery_flow(self, sm):
        """Test error and retry flow."""
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.TOOL_EXECUTING)
        sm.transition(ConversationState.ERROR, "Tool failed")
        sm.transition(ConversationState.PLANNING, "Retry")
        sm.transition(ConversationState.COMPLETED)
        assert sm.state == ConversationState.COMPLETED

    def test_timeout_not_triggered(self, sm):
        """Timeout should not trigger immediately."""
        sm = ConversationStateMachine(timeouts={ConversationState.PLANNING: 60.0})
        sm.transition(ConversationState.PLANNING)
        assert sm.is_timeout() is False

    def test_timeout_triggered(self, sm):
        """Timeout should trigger after threshold."""
        sm = ConversationStateMachine(timeouts={ConversationState.PLANNING: 0.001})
        sm.transition(ConversationState.PLANNING)
        import time
        time.sleep(0.01)
        assert sm.is_timeout() is True

    def test_interrupt(self, sm):
        """Test interrupt handling."""
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.TOOL_EXECUTING)
        sm.request_interrupt()
        assert sm.is_interrupted is True

    def test_reset(self, sm):
        """Reset should clear state and history."""
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.COMPLETED)
        sm.reset()
        assert sm.state == ConversationState.IDLE
        assert len(sm.transition_history) == 0

    def test_branch_create_and_merge(self, sm):
        """Test conversation branching."""
        sm.transition(ConversationState.PLANNING)
        branch = sm.create_branch("parallel_tools")
        assert branch.branch_id == "parallel_tools"
        assert branch.parent_state == ConversationState.PLANNING

        sm.merge_branch("parallel_tools", {"result": "ok"})
        results = sm.get_branch_results("parallel_tools")
        assert results == [{"result": "ok"}]

    def test_get_summary(self, sm):
        """Test state summary."""
        sm.transition(ConversationState.PLANNING, "Test")
        summary = sm.get_summary()
        assert summary["current_state"] == "PLANNING"
        assert summary["transition_count"] == 1
        assert summary["is_interrupted"] is False
        assert summary["last_transition"]["from"] == "IDLE"
        assert summary["last_transition"]["to"] == "PLANNING"

    def test_stopped_to_idle_restart(self, sm):
        """Test STOPPED -> IDLE restart."""
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.STOPPED, "User stopped")
        sm.transition(ConversationState.IDLE, "New conversation")
        assert sm.state == ConversationState.IDLE

    def test_incomplete_state(self, sm):
        """Test max iterations reached."""
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.SYNTHESIZING)
        sm.transition(ConversationState.INCOMPLETE, "Max iterations")
        assert sm.state == ConversationState.INCOMPLETE

    def test_all_valid_transitions(self, sm):
        """Verify all documented transitions work."""
        # IDLE -> PLANNING -> TOOL_EXECUTING -> SYNTHESIZING -> COMPLETED
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.TOOL_EXECUTING)
        sm.transition(ConversationState.SYNTHESIZING)
        sm.transition(ConversationState.COMPLETED)

        # COMPLETED -> IDLE -> STOPPED
        sm.transition(ConversationState.IDLE)
        sm.transition(ConversationState.STOPPED)

        # STOPPED -> IDLE -> PLANNING -> AWAITING_USER_INPUT -> PLANNING -> COMPLETED
        sm.transition(ConversationState.IDLE)
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.AWAITING_USER_INPUT, "Need clarification")
        sm.transition(ConversationState.PLANNING, "User responded")
        sm.transition(ConversationState.COMPLETED)
        assert sm.state == ConversationState.COMPLETED

    def test_parallel_branching(self, sm):
        """Test parallel execution branches."""
        sm.transition(ConversationState.PLANNING)
        branch1 = sm.create_branch("branch1")
        branch2 = sm.create_branch("branch2")

    def test_parallel_branching(self, sm):
        """Test parallel execution branches."""
        sm.transition(ConversationState.PLANNING)
        branch1 = sm.create_branch("branch1")
        branch2 = sm.create_branch("branch2")

        assert branch1.parent_state == ConversationState.PLANNING
        assert branch2.parent_state == ConversationState.PLANNING

        sm.merge_branch("branch1", {"status": "executed"})
        sm.merge_branch("branch2", {"status": "synthesized"})

        results = sm.get_branch_results("branch1")
        assert results == [{"status": "executed"}]

        results = sm.get_branch_results("branch2")
        assert results == [{"status": "synthesized"}]

    def test_cannot_create_duplicate_branch(self, sm):
        """Duplicate branch IDs should overwrite (not raise)."""
        sm.transition(ConversationState.PLANNING)
        sm.create_branch("dup")
        # Second create overwrites
        sm.create_branch("dup")
        assert len(sm._branches) == 1

    def test_cannot_merge_nonexistent_branch(self, sm):
        """Merging non-existent branch should silently do nothing."""
        sm.merge_branch("nonexistent", {})  # No error
        assert sm.get_branch_results("nonexistent") == []

    def test_interrupt_reason(self, sm):
        """Interrupt reason is not stored (simplified API)."""
        sm.request_interrupt()
        assert sm.is_interrupted is True
        sm.clear_interrupt()
        assert sm.is_interrupted is False

    def test_transition_metadata(self, sm):
        """Transition metadata not supported in current API."""
        sm.transition(ConversationState.PLANNING, "Start")
        history = sm.transition_history
        assert history[0].from_state == ConversationState.IDLE

    def test_complex_conversation_flow(self, sm):
        """Test a realistic multi-turn conversation."""
        # IDLE -> PLANNING -> AWAITING_USER_INPUT -> PLANNING -> COMPLETED
        sm.transition(ConversationState.PLANNING)
        sm.transition(ConversationState.AWAITING_USER_INPUT, "Need clarification")
        sm.transition(ConversationState.PLANNING, "User provided input")
        sm.transition(ConversationState.COMPLETED, "Done")
        assert sm.state == ConversationState.COMPLETED
        history = sm.transition_history
        assert len(history) == 4
