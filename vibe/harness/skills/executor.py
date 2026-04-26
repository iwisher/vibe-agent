"""Skill executor with environment variable support and template rendering.

Supports:
- Environment variable substitution in skill content
- Jinja2 template rendering
- Safe execution with timeout and shell injection hardening
"""

import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Optional

from vibe.harness.instructions import Skill


@dataclass
class ExecutionResult:
    """Result of skill execution."""
    success: bool
    output: str
    error: Optional[str] = None
    exit_code: int = 0


class SkillExecutor:
    """Execute skills with env var substitution and template rendering.

    Features:
    - Environment variable substitution: ${VAR} or $VAR
    - Jinja2 template rendering for dynamic content
    - Safe execution with configurable timeout
    - Shell injection hardening (command sanitization, blocked patterns)
    - Output capture and error handling
    """

    # Dangerous shell patterns that are blocked
    DANGEROUS_PATTERNS = [
        r"rm\s+-rf\s+/",
        r">\s*/dev/null",
        r">\s*/[a-z/]+",
        r"<\s*/[a-z/]+",
        r"\|\s*sh",
        r"\|\s*bash",
        r"`[^`]+`",
        r"\$\([^)]+\)",
        r";\s*rm\s",
        r"&&\s*rm\s",
        r"\|\s*rm\s",
        r"wget\s.*\|\s*sh",
        r"curl\s.*\|\s*sh",
        r"eval\s*\(",
        r"exec\s*\(",
    ]

    def __init__(
        self,
        timeout: float = 30.0,
        env: Optional[dict[str, str]] = None,
        blocked_commands: Optional[list[str]] = None,
    ):
        self.timeout = timeout
        self.env = env or dict(os.environ)
        self.blocked_commands = blocked_commands or []
        self._jinja_env = None

    def _get_jinja(self):
        """Lazy-load Jinja2 environment."""
        if self._jinja_env is None:
            try:
                from jinja2 import Environment, BaseLoader
                self._jinja_env = Environment(loader=BaseLoader())
            except ImportError:
                self._jinja_env = False
        return self._jinja_env

    def _sanitize_command(self, content: str) -> tuple[bool, str]:
        """Sanitize command content for dangerous patterns.

        Returns:
            (is_safe, error_message)
        """
        # Check blocked commands list
        for blocked in self.blocked_commands:
            if blocked.lower() in content.lower():
                return False, f"Blocked: command contains blocked pattern '{blocked}'"

        # Check dangerous regex patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return False, f"Blocked: dangerous command pattern detected ({pattern})"

        return True, ""

    def _substitute_env_vars(self, content: str) -> str:
        """Substitute environment variables in content.

        Supports:
        - ${VAR} syntax
        - $VAR syntax
        - Default values: ${VAR:-default}

        All substituted values are shlex-quoted to prevent injection.
        """
        # ${VAR:-default} syntax
        def replace_with_default(match):
            var_name = match.group(1)
            default = match.group(2) or ""
            value = self.env.get(var_name, default)
            return shlex.quote(value)

        content = re.sub(r'\$\{(\w+):-([^}]*)\}', replace_with_default, content)

        # ${VAR} syntax
        def replace_var(match):
            var_name = match.group(1)
            value = self.env.get(var_name)
            if value is not None:
                return shlex.quote(value)
            return match.group(0)  # Keep original if not found

        content = re.sub(r'\$\{(\w+)\}', replace_var, content)

        # $VAR syntax (but not $$ which is escaped)
        def replace_dollar_var(match):
            var_name = match.group(1)
            value = self.env.get(var_name)
            if value is not None:
                return shlex.quote(value)
            return match.group(0)  # Keep original if not found

        content = re.sub(r'\$(\w+)', replace_dollar_var, content)

        return content

    def _render_template(self, content: str, context: Optional[dict[str, Any]] = None) -> str:
        """Render Jinja2 template with context."""
        jinja = self._get_jinja()
        if jinja is False:
            # Jinja2 not available, return content as-is
            return content

        try:
            template = jinja.from_string(content)
            return template.render(**(context or {}))
        except Exception:
            # Template rendering failed, return original content
            return content

    def execute(
        self,
        skill: Skill,
        context: Optional[dict[str, Any]] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> ExecutionResult:
        """Execute a skill with env var substitution and template rendering.

        Args:
            skill: The skill to execute
            context: Template context variables
            extra_env: Additional environment variables

        Returns:
            ExecutionResult with output and status
        """
        # Merge extra env into current env
        if extra_env:
            self.env.update(extra_env)

        # Substitute env vars
        content = self._substitute_env_vars(skill.content)

        # Render template
        content = self._render_template(content, context)

        # Execute (for now, just return the processed content)
        # In a real implementation, this would execute the skill content
        return ExecutionResult(
            success=True,
            output=content,
            exit_code=0,
        )

    def execute_shell(
        self,
        skill: Skill,
        context: Optional[dict[str, Any]] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> ExecutionResult:
        """Execute a skill as a shell command.

        Args:
            skill: The skill containing shell commands
            context: Template context variables
            extra_env: Additional environment variables

        Returns:
            ExecutionResult with command output
        """
        import subprocess

        # Merge extra env
        env = {**self.env, **(extra_env or {})}

        # Substitute env vars
        content = self._substitute_env_vars(skill.content)

        # Render template
        content = self._render_template(content, context)

        # Sanitize command
        is_safe, error_msg = self._sanitize_command(content)
        if not is_safe:
            return ExecutionResult(
                success=False,
                output="",
                error=error_msg,
                exit_code=-1,
            )

        # Determine execution strategy
        # Use shell=False for simple commands (safer), shell=True only when needed
        has_shell_metacharacters = any(c in content for c in "|&;<>()$`\"\\'")
        # Shell builtins (exit, cd, export, etc.) require shell=True
        shell_builtins = {"exit", "cd", "export", "unset", "alias", "source", ".", "eval", "exec"}
        first_word = content.strip().split()[0] if content.strip() else ""
        is_shell_builtin = first_word in shell_builtins
        use_shell = has_shell_metacharacters or is_shell_builtin

        try:
            if use_shell:
                result = subprocess.run(
                    content,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            else:
                # Safer: split into args, no shell
                args = shlex.split(content)
                result = subprocess.run(
                    args,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            return ExecutionResult(
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr if result.stderr else None,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Command timed out after {self.timeout}s",
                exit_code=-1,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1,
            )
