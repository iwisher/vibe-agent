"""Tests for SkillExecutor."""

import pytest

from vibe.harness.instructions import Skill
from vibe.harness.skills.executor import ExecutionResult, SkillExecutor


class TestSkillExecutor:
    """Test SkillExecutor functionality."""

    def test_string_template_substitution(self):
        """Should substitute variables via string.Template."""
        executor = SkillExecutor(env={"TEST_VAR": "hello", "EMPTY_VAR": ""})
        mapping = executor._build_substitution_mapping()

        # ${VAR} syntax
        result = executor._substitute_template("Value is ${TEST_VAR}", mapping)
        assert result == "Value is hello"

        # $VAR syntax
        result = executor._substitute_template("Value is $TEST_VAR", mapping)
        assert result == "Value is hello"

    def test_string_template_default(self):
        """Should use default value for missing vars."""
        executor = SkillExecutor(env={"SET_VAR": "set"})
        mapping = executor._build_substitution_mapping()

        # ${VAR:-default} syntax
        result = executor._substitute_template("Value is ${MISSING:-default}", mapping)
        assert result == "Value is default"

        # Existing var should not use default
        result = executor._substitute_template("Value is ${SET_VAR:-default}", mapping)
        assert result == "Value is set"

    def test_string_template_missing_raises(self):
        """Should raise KeyError on missing variables."""
        executor = SkillExecutor(env={})
        mapping = executor._build_substitution_mapping()

        with pytest.raises(KeyError):
            executor._substitute_template("Value is ${MISSING}", mapping)

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
        assert result.error is None

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

    def test_execute_missing_variable_returns_error(self):
        """Should return error result when template variable is missing."""
        executor = SkillExecutor(env={})

        skill = Skill(
            name="test_skill",
            description="Test skill",
            content="Value is ${MISSING}",
            tags=["test"],
        )

        result = executor.execute(skill)
        assert result.success is False
        assert "Missing template variable" in (result.error or "")
        assert result.exit_code == -1

    def test_mixed_string_template_and_jinja2(self):
        """Should apply string.Template first, then Jinja2."""
        executor = SkillExecutor(env={"USER": "alice"})

        skill = Skill(
            name="mixed",
            description="Mixed substitution",
            content="User: $USER, Mode: {{ mode }}",
            tags=["test"],
        )

        result = executor.execute(skill, context={"mode": "dev"})
        assert result.success is True
        assert "User: alice" in result.output
        assert "Mode: dev" in result.output

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
