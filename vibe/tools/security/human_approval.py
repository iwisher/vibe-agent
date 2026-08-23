"""Human approval system for vibe-agent.

CLI mode: prompt_toolkit-style UI with 60-second timeout.
Choices: once | session | always | deny | view

Fail-closed: timeout -> deny (not allow).
"""

import os
import shlex
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None

from vibe.tools.security.approval_store import ApprovalStore

# Shared console for the approval banner. highlight=False keeps Rich from
# recoloring numbers/paths inside the flagged command text.
_console = Console(highlight=False)

# Approval UI hook contract:
#   fn(command, pattern_id, description, severity, cwd, timeout_seconds)
#       -> normalized token ("once" | "session" | "always" | "deny" | "view"
#       | "timeout"), or None to decline handling (legacy terminal UI is used).
# A hook that raises is treated as fail-closed deny — never as a reason to fall
# back to raw stdin reads while another UI (e.g. prompt_toolkit) may own the
# terminal.
ApprovalUIHook = Callable[[str, Optional[str], str, str, str, int], Optional[str]]

_ui_hook: ApprovalUIHook | None = None


def set_approval_ui_hook(fn: ApprovalUIHook) -> None:
    """Register a UI hook for interactive approval prompts."""
    global _ui_hook
    _ui_hook = fn


def reset_approval_ui_hook() -> None:
    """Remove any registered UI hook, restoring the legacy terminal UI."""
    global _ui_hook
    _ui_hook = None


def _render_panel(
    command: str,
    pattern_id: Optional[str],
    description: str,
    severity: str,
) -> None:
    """Print the approval warning panel and the question line."""
    severity_style = "bold red" if severity.lower() in ("critical", "high") else "yellow"
    body = Text()
    body.append("Severity: ").append(severity.upper(), style=severity_style).append("\n")
    if description:
        body.append(f"Reason: {description}\n")
    if pattern_id:
        body.append(f"Pattern: {pattern_id}\n")
    body.append("\nCommand:\n").append(command, style="bold")
    _console.print()
    _console.print(
        Panel(
            body,
            title="[bold red]SECURITY WARNING[/bold red]: Flagged command detected",
            border_style="red",
        )
    )
    _console.print("Approve? [o]nce / [s]ession / [a]lways / [d]eny / [v]iew", markup=False)


def _read_choice(timeout_seconds: int) -> str:
    """Read a raw choice string from stdin with a timeout.

    Returns the raw choice string (lower-cased in the non-tty fallback), or ""
    on timeout/EOF/interrupt.
    """
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
                            if char == "\n" or char == "\r":
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
    event.wait(timeout=timeout_seconds)

    # Signal the thread to stop if still running
    stop_event.set()

    # Give daemon thread a moment to exit
    thread.join(timeout=0.5)

    if not result:
        return ""
    return result[0]


def _normalize_choice(choice_str: str) -> str:
    """Normalize a raw choice string to a token for `_map_choice`."""
    if choice_str in ("o", "once"):
        return "once"
    if choice_str in ("s", "session"):
        return "session"
    if choice_str in ("a", "always"):
        return "always"
    if choice_str in ("v", "view"):
        return "view"
    return "deny"


def render_and_read_choice(
    command: str,
    pattern_id: Optional[str],
    description: str,
    severity: str,
    timeout_seconds: int,
) -> str:
    """Render the approval panel, read a choice, and return a normalized token.

    Tokens: "once" | "session" | "always" | "deny" | "view" | "timeout".
    Shared by the legacy terminal path and by prompt_toolkit-aware UI hooks
    (which call this with the terminal temporarily released).
    """
    _render_panel(command, pattern_id, description, severity)
    choice_str = _read_choice(timeout_seconds)
    if not choice_str:
        # Timeout/EOF - fail closed
        _console.print(f"\n[yellow]Timeout ({timeout_seconds}s). Denying command.[/yellow]")
        return "timeout"
    return _normalize_choice(choice_str)


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

        if command in self._session_approved_commands:
            return ApprovalResult(
                approved=True,
                choice=ApprovalChoice.SESSION,
                reason="Command approved for this session",
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
        """Show interactive prompt with timeout.

        When a UI hook is registered (e.g. a prompt_toolkit-aware CLI), the
        hook renders/reads with the terminal properly released. A hook
        returning None declines, falling back to the legacy terminal UI. A
        hook that raises fails closed — falling back to raw stdin reads while
        prompt_toolkit owns the terminal would corrupt its input area.
        """
        hook = _ui_hook
        if hook is not None:
            try:
                token = hook(command, pattern_id, description, severity, cwd, self.timeout_seconds)
            except Exception:
                return ApprovalResult(
                    approved=False,
                    choice=ApprovalChoice.DENY,
                    reason="Approval UI hook failed; denying command (fail-closed)",
                    pattern_id=pattern_id,
                )
            if token is not None:
                return self._map_choice(token, command, pattern_id, cwd, description, severity)

        token = render_and_read_choice(
            command, pattern_id, description, severity, self.timeout_seconds
        )
        return self._map_choice(token, command, pattern_id, cwd, description, severity)

    def _map_choice(
        self,
        choice_str: str,
        command: str,
        pattern_id: Optional[str],
        cwd: str,
        description: str = "",
        severity: str = "warning",
    ) -> ApprovalResult:
        """Map a choice string (raw input or normalized token) to an ApprovalResult.

        Carries out session/persistent approval side effects. The `view`
        choice prints the full command and re-prompts via `_interactive_prompt`,
        which re-enters the UI hook path when a hook is registered.
        """
        if choice_str == "timeout":
            return ApprovalResult(
                approved=False,
                choice=ApprovalChoice.DENY,
                reason=f"Timeout after {self.timeout_seconds}s (fail-closed)",
                pattern_id=pattern_id,
            )

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
            self._session_approved_commands.add(command)
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
            _console.print(f"\nFull command:\n{command}\n", markup=False)
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

    def reset_session(self) -> None:
        """Clear all session-level approvals."""
        self._session_approved_patterns.clear()
        self._session_approved_commands.clear()
