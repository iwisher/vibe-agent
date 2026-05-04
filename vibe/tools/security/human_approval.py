"""Human approval system for vibe-agent.

CLI mode: prompt_toolkit-style UI with 60-second timeout.
Choices: once | session | always | deny | view

Fail-closed: timeout -> deny (not allow).
"""

import sys
import threading
import os
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Optional

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None

from vibe.tools.security.approval_store import ApprovalStore


class ApprovalChoice(Enum):
    """User approval choices."""

    ONCE = "once"  # Approve this command only
    SESSION = "session"  # Approve for this session
    ALWAYS = "always"  # Approve this pattern permanently
    DENY = "deny"  # Deny this command
    VIEW = "view"  # View command details first


class ApprovalMode(Enum):
    """Approval mode configuration."""

    INTERACTIVE = "interactive"  # Prompt user for each flagged command
    AUTO = "auto"  # Auto-approve (with loud warning)
    STRICT = "strict"  # Deny all flagged commands


@dataclass
class ApprovalResult:
    """Result of an approval request."""

    approved: bool
    choice: Optional[ApprovalChoice]
    reason: str
    pattern_id: Optional[str] = None
    command_hash: Optional[str] = None


class HumanApprover:
    """Human approval system with timeout support."""

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.INTERACTIVE,
        timeout_seconds: int = 60,
    ):
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self._session_approved_patterns: set[str] = set()
        self._session_approved_commands: set[str] = set()
        self.store = ApprovalStore()

    def request_approval(
        self,
        command: str,
        pattern_id: Optional[str] = None,
        description: str = "",
        severity: str = "warning",
        cwd: Optional[str] = None,
    ) -> ApprovalResult:
        """Request human approval for a flagged command.

        Returns ApprovalResult with approved=True if user approves.
        """
        if self.mode == ApprovalMode.AUTO:
            return ApprovalResult(
                approved=True,
                choice=None,
                reason="AUTO mode: approval bypassed (set VIBE_APPROVAL_MODE=auto)",
                pattern_id=pattern_id,
            )

        if self.mode == ApprovalMode.STRICT:
            return ApprovalResult(
                approved=False,
                choice=ApprovalChoice.DENY,
                reason="STRICT mode: all flagged commands denied",
                pattern_id=pattern_id,
            )

        # Check session-level approvals
        if pattern_id and pattern_id in self._session_approved_patterns:
            return ApprovalResult(
                approved=True,
                choice=ApprovalChoice.SESSION,
                reason="Pattern approved for this session",
                pattern_id=pattern_id,
            )

        # Check persistent approvals
        check_cwd = cwd or os.getcwd()
        if self.store.check_approval(command, check_cwd):
            return ApprovalResult(
                approved=True,
                choice=ApprovalChoice.ALWAYS,
                reason="Command approved permanently in this path hierarchy",
                pattern_id=pattern_id,
            )

        # Interactive prompt
        return self._interactive_prompt(command, pattern_id, description, severity, check_cwd)

    def _interactive_prompt(
        self,
        command: str,
        pattern_id: Optional[str],
        description: str,
        severity: str,
        cwd: str,
    ) -> ApprovalResult:
        """Show interactive prompt with timeout."""
        print(f"\n{'=' * 60}")
        print("SECURITY WARNING: Flagged command detected")
        print(f"Severity: {severity.upper()}")
        if description:
            print(f"Reason: {description}")
        if pattern_id:
            print(f"Pattern: {pattern_id}")
        print(f"{'=' * 60}")
        print(f"Command: {command}")
        print(f"{'=' * 60}")
        print("Approve? [o]nce / [s]ession / [a]lways / [d]eny / [v]iew")

        # Simple input with timeout using threading
        result: list[str] = []
        event = threading.Event()
        stop_event = threading.Event()

        def read_input():
            try:
                # Use select for non-blocking input with timeout support
                import select
                if termios and tty and sys.stdin.isatty():
                    old_settings = termios.tcgetattr(sys.stdin)
                    try:
                        tty.setcbreak(sys.stdin.fileno())
                        while not stop_event.is_set():
                            if select.select([sys.stdin], [], [], 0.1)[0]:
                                char = sys.stdin.read(1)
                                if char == '\n' or char == '\r':
                                    break
                                result.append(char)
                    finally:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                else:
                    # Fallback to regular input
                    print("Choice: ", end="", flush=True)
                    line = sys.stdin.readline().strip().lower()
                    result.append(line)
            except (EOFError, KeyboardInterrupt):
                result.append("")
            event.set()

        thread = threading.Thread(target=read_input, daemon=True)
        thread.start()
        event.wait(timeout=self.timeout_seconds)

        # Signal the thread to stop if still running
        stop_event.set()

        # Give daemon thread a moment to exit
        thread.join(timeout=0.5)

        if not result:
            # Timeout - fail closed
            print(f"\nTimeout ({self.timeout_seconds}s). Denying command.")
            return ApprovalResult(
                approved=False,
                choice=ApprovalChoice.DENY,
                reason=f"Timeout after {self.timeout_seconds}s (fail-closed)",
                pattern_id=pattern_id,
            )

        choice_str = result[0]

        if choice_str in ("o", "once"):
            return ApprovalResult(
                approved=True,
                choice=ApprovalChoice.ONCE,
                reason="Approved for this execution",
                pattern_id=pattern_id,
            )
        elif choice_str in ("s", "session"):
            if pattern_id:
                self._session_approved_patterns.add(pattern_id)
            return ApprovalResult(
                approved=True,
                choice=ApprovalChoice.SESSION,
                reason="Approved for this session",
                pattern_id=pattern_id,
            )
        elif choice_str in ("a", "always"):
            if self.store.is_safe_command(command):
                self.store.add_scoped_approval(shlex.split(command)[0], cwd)
            else:
                self.store.add_exact_approval(command)
            return ApprovalResult(
                approved=True,
                choice=ApprovalChoice.ALWAYS,
                reason="Approved permanently (stored in approval store)",
                pattern_id=pattern_id,
            )
        elif choice_str in ("v", "view"):
            print(f"\nFull command:\n{command}\n")
            # Re-prompt
            return self._interactive_prompt(command, pattern_id, description, severity, cwd)
        else:
            # Default deny
            return ApprovalResult(
                approved=False,
                choice=ApprovalChoice.DENY,
                reason="User denied",
                pattern_id=pattern_id,
            )

    def is_auto_mode(self) -> bool:
        """Check if running in auto-approval mode."""
        return self.mode == ApprovalMode.AUTO
