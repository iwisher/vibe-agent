"""Tests for the approval UI hook mechanism in human_approval.

The hook lets a prompt_toolkit-owning CLI render approval prompts with the
terminal properly released (see vibe/cli/main.py). These tests pin the hook
contract and verify the legacy terminal path is unchanged when no hook is
registered.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from vibe.tools.security.human_approval import (
    ApprovalChoice,
    ApprovalMode,
    HumanApprover,
    reset_approval_ui_hook,
    set_approval_ui_hook,
)


@pytest.fixture
def approver():
    """Interactive approver with a guaranteed-clean hook registry."""
    reset_approval_ui_hook()
    ap = HumanApprover(mode=ApprovalMode.INTERACTIVE, timeout_seconds=42)
    yield ap
    reset_approval_ui_hook()


class RecordingHook:
    """Hook double: records calls, replays scripted tokens."""

    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.calls = []

    def __call__(self, command, pattern_id, description, severity, cwd, timeout_seconds):
        self.calls.append((command, pattern_id, description, severity, cwd, timeout_seconds))
        return self.tokens.pop(0)


class TestHookDelegation:
    def test_hook_receives_exact_args(self, approver):
        hook = RecordingHook(["once"])
        set_approval_ui_hook(hook)

        approver.request_approval(
            "rm -rf /tmp/x",
            pattern_id="rm-rf",
            description="bash tool call",
            severity="critical",
            cwd="/tmp",
        )

        assert hook.calls == [("rm -rf /tmp/x", "rm-rf", "bash tool call", "critical", "/tmp", 42)]

    def test_once_token_approves(self, approver):
        set_approval_ui_hook(RecordingHook(["once"]))
        result = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert result.approved
        assert result.choice == ApprovalChoice.ONCE
        assert result.pattern_id == "p1"
        # No session side effects
        assert "p1" not in approver._session_approved_patterns
        assert "rm a" not in approver._session_approved_commands

    def test_session_token_approves_and_caches(self, approver):
        hook = RecordingHook(["session"])
        set_approval_ui_hook(hook)
        result = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert result.approved
        assert result.choice == ApprovalChoice.SESSION
        assert "p1" in approver._session_approved_patterns
        assert "rm a" in approver._session_approved_commands

        # Second request is served from the session cache without re-prompting.
        result2 = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert result2.approved
        assert len(hook.calls) == 1

    def test_always_token_approves_and_persists(self, approver, tmp_path):
        store_path = tmp_path / ".vibe" / "approvals.json"
        with patch("vibe.tools.security.approval_store.DEFAULT_STORE_PATH", store_path):
            ap = HumanApprover(mode=ApprovalMode.INTERACTIVE)
            set_approval_ui_hook(RecordingHook(["always"]))
            result = ap.request_approval("ls -la", cwd=str(tmp_path))
            assert result.approved
            assert result.choice == ApprovalChoice.ALWAYS
            assert ap.store.check_approval("ls", str(tmp_path))
            assert Path(store_path).exists()

    def test_deny_token_denies(self, approver):
        set_approval_ui_hook(RecordingHook(["deny"]))
        result = approver.request_approval("rm a", cwd="/tmp")
        assert not result.approved
        assert result.choice == ApprovalChoice.DENY
        assert result.reason == "User denied"

    def test_timeout_token_fails_closed(self, approver):
        set_approval_ui_hook(RecordingHook(["timeout"]))
        result = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert not result.approved
        assert result.choice == ApprovalChoice.DENY
        assert "Timeout after 42s" in result.reason

    def test_view_token_reenters_hook(self, approver):
        hook = RecordingHook(["view", "once"])
        set_approval_ui_hook(hook)
        result = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert result.approved
        assert result.choice == ApprovalChoice.ONCE
        # The re-prompt after 'view' went through the hook again.
        assert len(hook.calls) == 2
        assert hook.calls[0] == hook.calls[1]


class TestHookFallbackAndFailure:
    def test_hook_none_uses_legacy_path(self, approver):
        hook = RecordingHook([None])
        set_approval_ui_hook(hook)
        with patch(
            "vibe.tools.security.human_approval.render_and_read_choice",
            return_value="once",
        ) as legacy:
            result = approver.request_approval("rm a", cwd="/tmp")
        assert result.approved
        assert result.choice == ApprovalChoice.ONCE
        legacy.assert_called_once()

    def test_hook_exception_denies_without_legacy_fallback(self, approver):
        def boom(*args):
            raise RuntimeError("terminal busy")

        set_approval_ui_hook(boom)
        with patch("vibe.tools.security.human_approval.render_and_read_choice") as legacy:
            result = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert not result.approved
        assert result.choice == ApprovalChoice.DENY
        assert "fail-closed" in result.reason
        assert result.pattern_id == "p1"
        # Never fall back to raw stdin reads when a registered hook fails.
        legacy.assert_not_called()

    def test_reset_restores_legacy_path(self, approver):
        hook = RecordingHook(["once"])
        set_approval_ui_hook(hook)
        reset_approval_ui_hook()
        with patch(
            "vibe.tools.security.human_approval.render_and_read_choice",
            return_value="deny",
        ) as legacy:
            result = approver.request_approval("rm a", cwd="/tmp")
        assert not result.approved
        assert result.reason == "User denied"
        legacy.assert_called_once()
        assert hook.calls == []


class TestLegacyPathUnchanged:
    """With no hook registered, the raw-input mapping still works end to end."""

    def _run_with_stdin(self, approver, keys):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdin.fileno", return_value=0),
            patch("vibe.tools.security.human_approval.termios"),
            patch("vibe.tools.security.human_approval.tty"),
            patch("select.select", return_value=([0], [], [])),
            patch("sys.stdin.read", side_effect=keys),
        ):
            return approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")

    def test_raw_choice_once(self, approver):
        result = self._run_with_stdin(approver, ["o", "\n"])
        assert result.approved
        assert result.choice == ApprovalChoice.ONCE

    def test_raw_choice_session_caches(self, approver):
        result = self._run_with_stdin(approver, ["s", "\n"])
        assert result.approved
        assert result.choice == ApprovalChoice.SESSION
        assert "p1" in approver._session_approved_patterns

    def test_raw_choice_deny(self, approver):
        result = self._run_with_stdin(approver, ["d", "\n"])
        assert not result.approved
        assert result.choice == ApprovalChoice.DENY

    def test_raw_timeout_denies(self, approver):
        # select never reports readable input -> read thread times out.
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdin.fileno", return_value=0),
            patch("vibe.tools.security.human_approval.termios"),
            patch("vibe.tools.security.human_approval.tty"),
            patch("select.select", return_value=([], [], [])),
        ):
            approver.timeout_seconds = 1
            result = approver.request_approval("rm a", pattern_id="p1", cwd="/tmp")
        assert not result.approved
        assert result.choice == ApprovalChoice.DENY
        assert "fail-closed" in result.reason
