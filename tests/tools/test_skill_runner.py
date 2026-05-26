"""Tests for SkillRunnerTool."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.tools.skill_runner import SkillRunnerTool
from vibe.tools.tool_system import ToolResult, ToolSystem


class FakeSkill:
    def __init__(self, steps, variables=None):
        self.steps = steps
        self.id = "test-skill"
        self.name = "Test Skill"
        self.variables = variables or []


class FakeStep:
    def __init__(self, command, tool="bash", verification=None):
        self.id = "step1"
        self.command = command
        self.tool = tool
        self.verification = verification or MagicMock()
        self.verification.exit_code = None
        self.verification.output_contains = None
        self.verification.file_exists = None


class TestSkillRunnerTool:
    def test_schema_has_required_fields(self):
        tool = SkillRunnerTool({}, ToolSystem())
        schema = tool.get_schema()
        assert "skill_id" in schema["properties"]
        assert "variables" in schema["properties"]
        assert "skill_id" in schema.get("required", [])

    @pytest.mark.asyncio
    async def test_missing_skill_id(self):
        tool = SkillRunnerTool({}, ToolSystem())
        result = await tool.execute(skill_id="missing")
        assert not result.success
        assert "not found" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_empty_steps_rejected(self):
        skill = FakeSkill(steps=[])
        tool = SkillRunnerTool({"test": skill}, ToolSystem())
        result = await tool.execute(skill_id="test")
        assert not result.success
        assert "no executable steps" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_successful_step_execution(self):
        tool_system = ToolSystem()
        mock_bash = MagicMock()
        mock_bash.name = "bash"
        mock_bash.execute = AsyncMock(return_value=ToolResult(
            success=True,
            content="hello world",
            metadata={"exit_code": 0},
        ))
        tool_system.register_tool(mock_bash)

        step = FakeStep(command="echo hello")
        skill = FakeSkill(steps=[step])
        tool = SkillRunnerTool({"test": skill}, tool_system)

        result = await tool.execute(skill_id="test")
        assert result.success
        assert "hello world" in str(result.content)

    @pytest.mark.asyncio
    async def test_step_failure_stops_execution(self):
        tool_system = ToolSystem()
        mock_bash = MagicMock()
        mock_bash.execute = AsyncMock(return_value=ToolResult(
            success=False,
            content="",
            error="Command failed",
            metadata={"exit_code": 1},
        ))
        tool_system.register_tool(mock_bash)
        mock_bash.name = "bash"

        step = FakeStep(command="false")
        skill = FakeSkill(steps=[step])
        tool = SkillRunnerTool({"test": skill}, tool_system)

        result = await tool.execute(skill_id="test")
        assert not result.success

    def test_substitute_vars_jinja2(self):
        result = SkillRunnerTool._substitute_vars("echo {{name}}", {"name": "world"})
        assert result == "echo world"

    def test_substitute_vars_shell_default(self):
        result = SkillRunnerTool._substitute_vars("echo ${NAME:-default}", {})
        assert result == "echo default"

    def test_substitute_vars_shell_declared(self):
        result = SkillRunnerTool._substitute_vars("echo ${NAME}", {"NAME": "world"})
        assert result == "echo world"

    def test_substitute_vars_unresolved_raises(self):
        with pytest.raises(ValueError, match="Unresolved variables"):
            SkillRunnerTool._substitute_vars("echo {{missing}}", {})

    def test_needs_shell_detects_metacharacters(self):
        assert SkillRunnerTool._needs_shell("cat file | grep x") is True
        assert SkillRunnerTool._needs_shell("echo hello") is False

    def test_needs_shell_detects_builtins(self):
        assert SkillRunnerTool._needs_shell("cd /tmp") is True
        assert SkillRunnerTool._needs_shell("ls") is False

    def test_verify_step_exit_code(self):
        result = ToolResult(success=True, content="", metadata={"exit_code": 0})
        verification = MagicMock()
        verification.exit_code = 0
        verification.output_contains = None
        verification.file_exists = None
        assert SkillRunnerTool._verify_step(result, verification, "cmd") is True

    def test_verify_step_output_contains(self):
        result = ToolResult(success=True, content="hello world", metadata={"exit_code": 0})
        verification = MagicMock()
        verification.exit_code = None
        verification.output_contains = "world"
        verification.file_exists = None
        assert SkillRunnerTool._verify_step(result, verification, "cmd") is True

    def test_verify_step_output_missing(self):
        result = ToolResult(success=True, content="hello", metadata={"exit_code": 0})
        verification = MagicMock()
        verification.exit_code = None
        verification.output_contains = "world"
        verification.file_exists = None
        assert SkillRunnerTool._verify_step(result, verification, "cmd") is False

    @pytest.mark.asyncio
    async def test_variables_validation_success(self):
        tool_system = ToolSystem()
        mock_bash = MagicMock()
        mock_bash.name = "bash"
        mock_bash.execute = AsyncMock(return_value=ToolResult(
            success=True,
            content="success",
            metadata={"exit_code": 0},
        ))
        tool_system.register_tool(mock_bash)

        variables = [
            {"name": "count", "type": "integer", "required": True, "minimum": 0},
            {"name": "tag", "type": "string", "default": "latest"},
        ]
        step = FakeStep(command="echo {{count}} {{tag}}")
        skill = FakeSkill(steps=[step], variables=variables)
        tool = SkillRunnerTool({"test": skill}, tool_system)

        # 1. Valid arguments (with type coercion of count)
        result = await tool.execute(skill_id="test", variables={"count": "5"})
        assert result.success is True
        assert "[OK] step1: success" in result.content

        # 2. Invalid arguments (out of range / validation error)
        result_invalid = await tool.execute(skill_id="test", variables={"count": "-1"})
        assert result_invalid.success is False
        assert "Variable validation failed" in result_invalid.error

        # 3. Missing required argument
        result_missing = await tool.execute(skill_id="test", variables={})
        assert result_missing.success is False
        assert "Variable 'count' is required" in result_missing.error
