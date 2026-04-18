"""Bash execution tool with sandboxing support."""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tool_system import Tool, ToolResult


# Dangerous pattern denylist (regex-based to catch obfuscation and variants)
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
    allowed_commands: Optional[List[str]] = field(default=None)
    dangerous_patterns: List[str] = field(default_factory=lambda: list(_DEFAULT_DANGEROUS_PATTERNS))

    def __post_init__(self):
        self.working_dir = str(Path(self.working_dir).resolve())
        self._dangerous_regexes = [re.compile(p, re.IGNORECASE) for p in self.dangerous_patterns]


class BashTool(Tool):
    """Execute bash commands in a sandboxed environment."""

    def __init__(self, sandbox: Optional[BashSandbox] = None):
        super().__init__(
            name="bash",
            description="Execute bash commands. Use for file operations, running scripts, and system tasks.",
        )
        self.sandbox = sandbox or BashSandbox()

    def get_schema(self) -> Dict[str, Any]:
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

    def _is_dangerous(self, command: str) -> Optional[str]:
        """Check if a command matches a dangerous pattern. Returns the matched pattern or None."""
        for regex in self.sandbox._dangerous_regexes:
            if regex.search(command):
                return regex.pattern
        return None

    def _check_whitelist(self, command: str) -> bool:
        """If allowed_commands is set, only permit commands that start with one of them."""
        if self.sandbox.allowed_commands is None:
            return True
        cmd_stripped = command.strip().lower()
        for allowed in self.sandbox.allowed_commands:
            if cmd_stripped.startswith(allowed.lower()):
                return True
        return False

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

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.sandbox.working_dir,
                capture_output=True,
                text=True,
                timeout=self.sandbox.timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            return ToolResult(
                success=result.returncode == 0,
                content=output.strip(),
                error=f"Exit code: {result.returncode}" if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                content=None,
                error=f"Command timed out after {self.sandbox.timeout}s",
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
