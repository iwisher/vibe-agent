"""Bash execution tool with sandboxing support."""

import asyncio
import os
import re
import shlex
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tool_system import Tool, ToolResult


# Dangerous pattern denylist (regex-based to catch obfuscation and variants)
# NOTE: This is a SECONDARY defense layer. The PRIMARY defense is:
# 1. Using create_subprocess_exec (no shell interpretation)
# 2. Rejecting unquoted shell metacharacters
# 3. Exact-token whitelist matching
_DEFAULT_DANGEROUS_PATTERNS = [
    # Filesystem destruction
    r"rm\s+-rf\s+/+",
    r"rm\s+-rf\s+/\s*\*",
    r">\s*/dev/sda",
    r"dd\s+if=/dev/zero\s+of=/dev/[sh]d",
    # Fork bombs
    r"\:\(\)\s*\{\s*\:\s*\|\s*\:\s*\&\s*\}\s*\;\s*\:",
    # Privilege escalation
    r"\bsudo\b",
    r"\bsu\b",
    r"\bdoas\b",
    # Arbitrary code execution via pipes
    r"(curl|wget|fetch)\s+[^|]*\|\s*(bash|sh|zsh|python|perl|ruby)",
    r"bash\s+.*<\s*\(\s*(curl|wget|fetch)",
    r"eval\s*\(",
    r"eval\s+[`\"']",
    r"\beval\s+\$",
    # Dangerous chmod
    r"chmod\s+[-+]?[0-7]*777\s+/+",
    # Network exfil / suspicious outbound
    r"nc\s+.*-e\s+(bash|sh|zsh|python)",
    r"bash\s+-i\s+>&\s+/dev/tcp",
]


@dataclass
class BashSandbox:
    """Sandbox configuration for bash execution."""

    working_dir: str = "."
    timeout: int = 120
    allowed_commands: list[str] | None = field(default=None)
    dangerous_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_DANGEROUS_PATTERNS))

    def __post_init__(self):
        self.working_dir = str(Path(self.working_dir).resolve())
        self._dangerous_regexes = [re.compile(p, re.IGNORECASE) for p in self.dangerous_patterns]


class BashTool(Tool):
    """Execute bash commands in a sandboxed environment."""

    def __init__(self, sandbox: BashSandbox | None = None):
        super().__init__(
            name="bash",
            description="Execute bash commands. Use for file operations, running scripts, and system tasks.",
        )
        self.sandbox = sandbox or BashSandbox()

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
            },
            "required": ["command"],
        }

    def _is_dangerous(self, command: str) -> str | None:
        """Check if a command matches a dangerous pattern. Returns the matched pattern or None."""
        for regex in self.sandbox._dangerous_regexes:
            if regex.search(command):
                return regex.pattern
        return None

    def _check_whitelist(self, command: str) -> bool:
        """If allowed_commands is set, only permit commands whose first token exactly matches."""
        if self.sandbox.allowed_commands is None:
            return True
        try:
            tokens = shlex.split(command.strip())
        except ValueError:
            return False  # Malformed command (e.g., unbalanced quotes)
        if not tokens:
            return False
        first_token = tokens[0].lower()
        for allowed in self.sandbox.allowed_commands:
            if first_token == allowed.lower():
                return True
        return False

    def _has_unquoted_shell_chars(self, command: str) -> str | None:
        """Return the first unquoted shell metacharacter found, or None if safe.

        Uses a simple state machine to track whether we're inside single quotes,
        double quotes, or an escape sequence. Only characters outside quotes count.
        """
        in_single_quote = False
        in_double_quote = False
        escaped = False

        for ch in command:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                continue
            if ch == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                continue
            if not in_single_quote and not in_double_quote:
                if ch in "|&;><$`":
                    return ch
        return None

    def _redirect_path(self, text: str) -> str:
        """Redirect /tmp/vibe_* paths to VIBE_EVAL_WORK_DIR if set.
        Validates that the resolved path stays within work_dir to prevent traversal."""
        work_dir = os.environ.get("VIBE_EVAL_WORK_DIR")
        if not work_dir:
            return text
        pattern = r"/tmp/(vibe_[^\s'\"]*)"

        def _repl(m):
            redirected = os.path.join(work_dir, m.group(1))
            resolved = os.path.realpath(redirected)
            resolved_work = os.path.realpath(work_dir)
            if not resolved.startswith(resolved_work + os.sep) and resolved != resolved_work:
                # Traversal attempt — keep original path, let it fail normally
                return m.group(0)
            return redirected

        return re.sub(pattern, _repl, text)

    async def execute(self, command: str, **kwargs) -> ToolResult:
        command = self._redirect_path(command)
        matched = self._is_dangerous(command)
        if matched:
            return ToolResult(
                success=False,
                content=None,
                error=f"Command blocked by safety policy (matched pattern: {matched}).",
            )

        if not self._check_whitelist(command):
            return ToolResult(
                success=False,
                content=None,
                error="Command not in allowed_commands whitelist.",
            )

        # PRIMARY defense: reject any command with unquoted shell metacharacters.
        # We use create_subprocess_exec (not shell), so pipes, redirects,
        # command chaining, and variable expansion are not supported.
        shell_char = self._has_unquoted_shell_chars(command)
        if shell_char:
            return ToolResult(
                success=False,
                content=None,
                error=(
                    f"Shell metacharacter '{shell_char}' detected. "
                    "Only simple commands are supported (no pipes, redirects, "
                    "command chaining, or variable expansion). "
                    "Split complex operations into multiple tool calls."
                ),
            )

        try:
            args = shlex.split(command)
            if not args:
                return ToolResult(success=False, content=None, error="Empty command.")

            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=self.sandbox.working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(), timeout=self.sandbox.timeout
            )
            output = stdout_data.decode(errors="replace") if stdout_data else ""
            if stderr_data:
                output += f"\n[stderr]\n{stderr_data.decode(errors='replace')}"
            return ToolResult(
                success=proc.returncode == 0,
                content=output.strip(),
                error=f"Exit code: {proc.returncode}" if proc.returncode != 0 else None,
            )
        except asyncio.TimeoutError:
            try:
                # Kill the entire process group to avoid orphaned children
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass  # Process already dead
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            return ToolResult(
                success=False,
                content=None,
                error=f"Command timed out after {self.sandbox.timeout}s",
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
