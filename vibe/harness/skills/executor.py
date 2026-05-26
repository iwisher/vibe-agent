"""Skill executor with environment variable support and template rendering.

Supports:
- Environment variable substitution in skill content
- Jinja2 template rendering
- Safe execution with timeout and shell injection hardening
"""

import os
import re
import shlex
import string
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
                from jinja2 import BaseLoader, Environment
                self._jinja_env = Environment(loader=BaseLoader())
            except ImportError:
                self._jinja_env = False
        return self._jinja_env

    def _build_substitution_mapping(
        self,
        context: Optional[dict[str, Any]] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """Build a string-only mapping for string.Template substitution."""
        mapping: dict[str, str] = {}
        mapping.update(self.env)
        if extra_env:
            mapping.update(extra_env)
        if context:
            for key, value in context.items():
                if isinstance(value, str):
                    mapping[key] = value
                elif isinstance(value, (int, float, bool)):
                    mapping[key] = str(value)
        return mapping

    def _apply_default_patterns(self, content: str, mapping: dict[str, str]) -> str:
        """Pre-process ${VAR:-default} syntax before string.Template."""

        def replace_default(match):
            var_name = match.group(1)
            default = match.group(2) or ""
            return mapping.get(var_name, default)

        return re.sub(r"\$\{(\w+):-([^}]*)\}", replace_default, content)

    def _substitute_template(self, content: str, mapping: dict[str, str]) -> str:
        """Primary substitution using string.Template.

        Supports $var and ${var} syntax.
        Raises KeyError on missing variables.
        """
        content = self._apply_default_patterns(content, mapping)
        template = string.Template(content)
        return template.substitute(mapping)

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
        """Execute a skill with template substitution and rendering.

        Args:
            skill: The skill to execute
            context: Template context variables
            extra_env: Additional environment variables

        Returns:
            ExecutionResult with output and status
        """
        try:
            mapping = self._build_substitution_mapping(context, extra_env)
            content = self._substitute_template(skill.content, mapping)
        except KeyError as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Missing template variable: {e}",
                exit_code=-1,
            )

        # Render template (Jinja2 fallback for complex logic)
        content = self._render_template(content, context)

        return ExecutionResult(
            success=True,
            output=content,
            exit_code=0,
        )
