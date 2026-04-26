"""Tests for SkillExecutor."""

import os
import pytest
from unittest.mock import patch

from vibe.harness.instructions import Skill
from vibe.harness.skills.executor import ExecutionResult, SkillExecutor


class TestSkillExecutor:
    """Test SkillExecutor functionality."""

    def test_env_var_substitution(self):
        """Should substitute environment variables."""
        executor = SkillExecutor(env={"TEST_VAR": "hello", "EMPTY_VAR": ""})

        # ${VAR} syntax
        result = executor._substitute_env_vars("Value is ${TEST_VAR}")
        assert result == "Value is hello"

        # $VAR syntax
        result = executor._substitute_env_vars("Value is $TEST_VAR")
        assert result == "Value is hello"

    def test_env_var_default(self):
        """Should use default value for missing env vars."""
        executor = SkillExecutor(env={"SET_VAR": "set"})

        # ${VAR:-default} syntax
        result = executor._substitute_env_vars("Value is ${MISSING:-default}")
        assert result == "Value is default"

        # Existing var should not use default
        result = executor._substitute_env_vars("Value is ${SET_VAR:-default}")
        assert result == "Value is set"

    def test_missing_env_var_preserved(self):
        """Should preserve syntax for missing env vars without default."""
        executor = SkillExecutor(env={})

        result = executor._substitute_env_vars("Value is ${MISSING}")
        assert result == "Value is ${MISSING}"

    def test_template_rendering(self):
        """Should render Jinja2 templates."""
        executor = SkillExecutor()

        content = "Hello {{ name }}!"
        result = executor._render_template(content, {"name": "World"})
        assert result == "Hello World!"

    def test_template_no_jinja(self):
        """Should handle content without Jinja2."""
        executor = SkillExecutor()

        content = "Plain text without templates"
        result = executor._render_template(content)
        assert result == "Plain text without templates"

    def test_execute_skill(self):
        """Should execute a skill."""
        executor = SkillExecutor(env={"API_KEY": "secret123"})

        skill = Skill(
            name="test_skill",
            description="Test skill",
            content="API key is ${API_KEY}",
            tags=["test"],
        )

        result = executor.execute(skill)
        assert result.success is True
        assert "secret123" in result.output

    def test_execute_with_context(self):
        """Should execute with template context."""
        executor = SkillExecutor()

        skill = Skill(
            name="test_skill",
            description="Test skill",
            content="Hello {{ name }}!",
            tags=["test"],
        )

        result = executor.execute(skill, context={"name": "Alice"})
        assert result.success is True
        assert "Hello Alice!" in result.output

    def test_execute_with_extra_env(self):
        """Should merge extra environment variables."""
        executor = SkillExecutor(env={"BASE": "base"})

        skill = Skill(
            name="test_skill",
            description="Test skill",
            content="Base: ${BASE}, Extra: ${EXTRA}",
            tags=["test"],
        )

        result = executor.execute(skill, extra_env={"EXTRA": "extra"})
        assert result.success is True
        assert "Base: base" in result.output
        assert "Extra: extra" in result.output

    def test_shell_execution(self):
        """Should execute shell commands."""
        executor = SkillExecutor(timeout=5.0)

        skill = Skill(
            name="echo_skill",
            description="Echo skill",
            content="echo 'hello world'",
            tags=["test"],
        )

        result = executor.execute_shell(skill)
        assert result.success is True
        assert "hello world" in result.output

    def test_shell_timeout(self):
        """Should handle shell timeout."""
        executor = SkillExecutor(timeout=0.1)

        skill = Skill(
            name="sleep_skill",
            description="Sleep skill",
            content="sleep 10",
            tags=["test"],
        )

        result = executor.execute_shell(skill)
        assert result.success is False
        assert "timed out" in result.error.lower()

    def test_shell_error(self):
        """Should handle shell errors."""
        executor = SkillExecutor()

        skill = Skill(
            name="error_skill",
            description="Error skill",
            content="exit 1",
            tags=["test"],
        )

        result = executor.execute_shell(skill)
        assert result.success is False
        assert result.exit_code == 1

    def test_execution_result(self):
        """Should create ExecutionResult."""
        result = ExecutionResult(success=True, output="test")
        assert result.success is True
        assert result.output == "test"
        assert result.error is None
        assert result.exit_code == 0
