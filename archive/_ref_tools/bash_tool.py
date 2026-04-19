"""Bash tool with sandboxing for command execution."""

import asyncio
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .tool_system import Tool, ToolResult


@dataclass
class BashSandbox:
    """Sandbox configuration for bash execution."""
    working_dir: str = "."
    allowed_commands: Optional[Set[str]] = None
    blocked_commands: Optional[Set[str]] = None
    timeout: int = 60
    max_output_size: int = 100000  # 100KB
    env_vars: Optional[Dict[str, str]] = None

    # Dangerous patterns that are always blocked
    DANGEROUS_PATTERNS = [
        r"rm\s+-rf\s+/",
        r"rm\s+-rf\s+~",
        r"rm\s+-rf\s+/\*",
        r">\s*/dev/sda",
        r"dd\s+if=/dev/zero",
        r"mkfs",
        r":\s*\(\)\s*\{\s*:\s*\|:&\s*\};\s*:",  # Fork bomb
        r"chmod\s+-R\s+777\s+/",
        r"mv\s+/\s+/dev/null",
    ]

    def __post_init__(self):
        if self.env_vars is None:
            self.env_vars = dict(os.environ)

    def is_command_safe(self, command: str) -> tuple[bool, Optional[str]]:
        """Check if a command is safe to execute.

        Returns:
            (is_safe, error_message)
        """
        command_lower = command.lower().strip()

        # Check dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command_lower):
                return False, f"Command blocked: matches dangerous pattern"

        # Check blocked commands
        if self.blocked_commands:
            cmd = shlex.split(command)[0] if command else ""
            if cmd in self.blocked_commands:
                return False, f"Command '{cmd}' is blocked"

        # Check allowed commands whitelist
        if self.allowed_commands:
            cmd = shlex.split(command)[0] if command else ""
            if cmd not in self.allowed_commands:
                return False, f"Command '{cmd}' is not in allowed list"

        return True, None


class BashTool(Tool):
    """Tool for executing bash commands with sandboxing."""

    def __init__(self, sandbox: Optional[BashSandbox] = None):
        super().__init__(
            name="bash",
            description="Execute bash commands in a sandboxed environment"
        )
        self.sandbox = sandbox or BashSandbox()

    def get_schema(self) -> Dict[str, Any]:
        """Return the tool schema."""
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (optional, default 60)",
                    "default": 60
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory for execution (optional)",
                    "default": "."
                }
            },
            "required": ["command"]
        }

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters before execution."""
        command = params.get("command")
        if not command:
            return False, "Command is required"
        if not isinstance(command, str):
            return False, "Command must be a string"
        return True, None

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the bash command."""
        command = kwargs.get("command")
        timeout = kwargs.get("timeout", self.sandbox.timeout)
        working_dir = kwargs.get("working_dir", self.sandbox.working_dir)

        # Safety check
        is_safe, error = self.sandbox.is_command_safe(command)
        if not is_safe:
            return ToolResult(success=False, content=None, error=error)

        try:
            # Run the command (await directly since we're already async)
            result = await self._run_command(command, timeout, working_dir)
            return result
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Execution error: {str(e)}"
            )

    async def _run_command(
        self,
        command: str,
        timeout: int,
        working_dir: str
    ) -> ToolResult:
        """Run a command asynchronously."""
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=self.sandbox.env_vars
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"Command timed out after {timeout} seconds"
                )

            # Decode output
            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            # Truncate if too large
            if len(stdout_str) > self.sandbox.max_output_size:
                stdout_str = stdout_str[:self.sandbox.max_output_size] + "\n... [truncated]"

            # Combine stdout and stderr
            output = stdout_str
            if stderr_str:
                output += f"\n[stderr]: {stderr_str}"

            success = process.returncode == 0
            error = None if success else f"Exit code: {process.returncode}"

            return ToolResult(
                success=success,
                content=output,
                error=error
            )

        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=str(e)
            )
