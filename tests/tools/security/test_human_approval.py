"""Tests for the human approval system."""

import pytest
from unittest.mock import patch

from vibe.tools.security.human_approval import (
    HumanApprover,
    ApprovalMode,
    ApprovalChoice,
    ApprovalResult,
)


class TestApprovalMode:
    """Test ApprovalMode enum."""

    def test_mode_values(self):
        assert ApprovalMode.INTERACTIVE.value == "interactive"
        assert ApprovalMode.AUTO.value == "auto"
        assert ApprovalMode.STRICT.value == "strict"


class TestHumanApprover:
    """Test HumanApprover."""

    def test_auto_mode_approves(self):
        """AUTO mode always approves."""
        approver = HumanApprover(mode=ApprovalMode.AUTO)
        result = approver.request_approval("rm -rf /", pattern_id="rm-rf-root")
        assert result.approved
        assert "AUTO mode" in result.reason

    def test_strict_mode_denies(self):
        """STRICT mode always denies."""
        approver = HumanApprover(mode=ApprovalMode.STRICT)
        result = approver.request_approval("ls", pattern_id="safe")
        assert not result.approved
        assert result.choice == ApprovalChoice.DENY
        assert "STRICT mode" in result.reason

    def test_session_approval_caches(self):
        """Session approval caches pattern for duration."""
        approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
        # Simulate session approval by adding directly
        approver._session_approved_patterns.add("test-pattern")
        result = approver.request_approval("ls", pattern_id="test-pattern")
        assert result.approved
        assert result.choice == ApprovalChoice.SESSION

    def test_is_auto_mode(self):
        approver = HumanApprover(mode=ApprovalMode.AUTO)
        assert approver.is_auto_mode()

        approver = HumanApprover(mode=ApprovalMode.INTERACTIVE)
        assert not approver.is_auto_mode()
