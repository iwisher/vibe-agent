"""Tests for SkillRunnerTool."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.harness.skills.models import Skill, SkillStep, SkillVerification
from vibe.tools.bash import BashSandbox, BashTool
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
        mock_bash.execute = AsyncMock(
            return_value=ToolResult(
                success=True,
                content="hello world",
                metadata={"exit_code": 0},
            )
        )
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
        mock_bash.execute = AsyncMock(
            return_value=ToolResult(
                success=False,
                content="",
                error="Command failed",
                metadata={"exit_code": 1},
            )
        )
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
        mock_bash.execute = AsyncMock(
            return_value=ToolResult(
                success=True,
                content="success",
                metadata={"exit_code": 0},
            )
        )
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

    def test_substitute_vars_spacing_insensitive(self):
        result = SkillRunnerTool._substitute_vars("echo {{ name  }}", {"name": "world"})
        assert result == "echo world"
        result2 = SkillRunnerTool._substitute_vars("echo {{name }}", {"name": "world"})
        assert result2 == "echo world"

    @pytest.mark.asyncio
    async def test_circular_execution_guard(self):
        tool_system = ToolSystem()
        step = FakeStep(command="nested", tool="run_skill")
        skill = FakeSkill(steps=[step])
        tool = SkillRunnerTool({"test-circular": skill}, tool_system)
        result = await tool.execute(skill_id="test-circular")
        assert result.success is False
        assert "Circular execution blocked" in result.error

    def test_verify_step_output_contains_with_substitution(self):
        result = ToolResult(
            success=True, content="hello world-substituted", metadata={"exit_code": 0}
        )
        verification = MagicMock()
        verification.exit_code = None
        verification.output_contains = "world-{{suffix}}"
        verification.file_exists = None
        assert (
            SkillRunnerTool._verify_step(result, verification, "cmd", {"suffix": "substituted"})
            is True
        )


class TestJsonHasKeysVerification:
    """_verify_step support for SkillVerification.json_has_keys."""

    def _verify(self, content: str, keys: list[str]) -> bool:
        result = ToolResult(success=True, content=content, metadata={"exit_code": 0})
        verification = SkillVerification(exit_code=0, json_has_keys=keys)
        return SkillRunnerTool._verify_step(result, verification, "cmd")

    def test_pass_when_all_keys_present(self):
        assert self._verify('{"ticker": "QQQ", "sma_20": 120.5}', ["ticker", "sma_20"])

    def test_fail_when_key_missing(self):
        assert not self._verify('{"ticker": "QQQ"}', ["ticker", "sma_20"])

    def test_fail_on_invalid_json(self):
        assert not self._verify("not json at all", ["ticker"])

    def test_fail_on_non_dict_json(self):
        assert not self._verify('["ticker", "sma_20"]', ["ticker"])

    def test_pass_with_stderr_suffix(self):
        # BashTool appends a "[stderr]" section; the JSON object must still be found.
        content = '{"ticker": "QQQ"}\n[stderr]\nsome warning'
        assert self._verify(content, ["ticker"])


def _make_script_skill_dir(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "my-skill"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "collect.py").write_text(
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n"
    )
    (scripts / "hello.sh").write_text("#!/bin/bash\necho shell-ok\n")
    return skill_dir


def _script_skill(
    skill_dir: Path | None,
    script: str = "scripts/collect.py",
    command: str = "{{ ticker }} --days {{ days }}",
    interpreter: str | None = None,
    verification: SkillVerification | None = None,
) -> Skill:
    return Skill(
        vibe_skill_version="2.0.0",
        id="s",
        name="S",
        description="d",
        steps=[
            SkillStep(
                id="run",
                description="run",
                script=script,
                interpreter=interpreter,
                tool="bash",
                command=command,
                verification=verification or SkillVerification(exit_code=0),
            )
        ],
        variables=[
            {"name": "ticker", "type": "string", "required": True},
            {"name": "days", "type": "integer", "required": False, "default": 30},
        ],
        skill_dir=str(skill_dir) if skill_dir else None,
    )


def _real_tool_system(working_dir: Path) -> ToolSystem:
    tool_system = ToolSystem()
    tool_system.register_tool(BashTool(sandbox=BashSandbox(working_dir=str(working_dir))))
    return tool_system


class TestScriptSteps:
    """Deterministic script steps: argv built by the runner, jailed to scripts/."""

    @pytest.mark.asyncio
    async def test_script_step_happy_path(self, tmp_path):
        skill_dir = _make_script_skill_dir(tmp_path)
        tool = SkillRunnerTool({"s": _script_skill(skill_dir)}, _real_tool_system(tmp_path))

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert result.success, result.error
        data = SkillRunnerTool._extract_json_object(result.content)
        # .py inferred sys.executable; days defaulted to 30 by the typed-vars schema
        assert data["argv"] == ["QQQ", "--days", "30"]

    @pytest.mark.asyncio
    async def test_script_step_shell_inference(self, tmp_path):
        skill_dir = _make_script_skill_dir(tmp_path)
        skill = _script_skill(
            skill_dir,
            script="scripts/hello.sh",
            command="",
            verification=SkillVerification(exit_code=0, output_contains="shell-ok"),
        )
        tool = SkillRunnerTool({"s": skill}, _real_tool_system(tmp_path))

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert result.success, result.error
        assert "shell-ok" in result.content

    @pytest.mark.asyncio
    async def test_script_step_jail_rejects_parent_escape(self, tmp_path):
        skill_dir = _make_script_skill_dir(tmp_path)
        (tmp_path / "evil.py").write_text("print('pwned')")
        tool = SkillRunnerTool(
            {"s": _script_skill(skill_dir, script="../evil.py")}, _real_tool_system(tmp_path)
        )

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert not result.success
        assert "outside" in (result.error or "")

    @pytest.mark.asyncio
    async def test_script_step_jail_rejects_absolute_path(self, tmp_path):
        skill_dir = _make_script_skill_dir(tmp_path)
        tool = SkillRunnerTool(
            {"s": _script_skill(skill_dir, script="/etc/hosts")}, _real_tool_system(tmp_path)
        )

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert not result.success
        assert "outside" in (result.error or "")

    @pytest.mark.asyncio
    async def test_script_step_missing_file(self, tmp_path):
        skill_dir = _make_script_skill_dir(tmp_path)
        tool = SkillRunnerTool(
            {"s": _script_skill(skill_dir, script="scripts/nope.py")},
            _real_tool_system(tmp_path),
        )

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert not result.success
        assert "script not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_script_step_missing_skill_dir(self, tmp_path):
        tool = SkillRunnerTool({"s": _script_skill(None)}, _real_tool_system(tmp_path))

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert not result.success
        assert "no skill_dir" in (result.error or "")

    @pytest.mark.asyncio
    async def test_script_step_unknown_extension_requires_interpreter(self, tmp_path):
        skill_dir = _make_script_skill_dir(tmp_path)
        (skill_dir / "scripts" / "tool.xyz").write_text("print('hi')")
        tool = SkillRunnerTool(
            {"s": _script_skill(skill_dir, script="scripts/tool.xyz")},
            _real_tool_system(tmp_path),
        )

        result = await tool.execute(skill_id="s", variables={"ticker": "QQQ"})

        assert not result.success
        assert "cannot infer interpreter" in (result.error or "")

    @pytest.mark.asyncio
    async def test_script_step_injection_payload_is_inert(self, tmp_path):
        """A metacharacter-laden variable must arrive as one inert argv token."""
        skill_dir = _make_script_skill_dir(tmp_path)
        marker = tmp_path / "pwned"
        payload = f"ABC; touch {marker}"
        tool = SkillRunnerTool({"s": _script_skill(skill_dir)}, _real_tool_system(tmp_path))

        result = await tool.execute(skill_id="s", variables={"ticker": payload})

        assert result.success, result.error
        assert not marker.exists()
        data = SkillRunnerTool._extract_json_object(result.content)
        assert data["argv"][0] == payload
        assert len(data["argv"]) == 3  # payload, --days, 30

    def test_substitute_vars_quote_mode(self):
        result = SkillRunnerTool._substitute_vars(
            "run.py {{ name }}", {"name": "a b; c"}, quote=True
        )
        assert result == "run.py 'a b; c'"
        # Default mode remains unquoted (backwards compatible)
        result_plain = SkillRunnerTool._substitute_vars("run.py {{ name }}", {"name": "a b"})
        assert result_plain == "run.py a b"


class TestStockAnalysisSkillIntegration:
    """End-to-end: the repo's stock-analysis skill runs its script against a CSV."""

    SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "stock-analysis"

    def _write_fixture(self, tmp_path: Path) -> Path:
        lines = ["Date,Close"] + [f"2026-01-{day:02d},{99 + day}" for day in range(1, 31)]
        csv_path = tmp_path / "prices.csv"
        csv_path.write_text("\n".join(lines) + "\n")
        return csv_path

    def test_skill_parses_and_validates(self):
        from vibe.harness.skills.parser import SkillParser
        from vibe.harness.skills.validator import SkillValidator

        skill = SkillParser().parse_file(self.SKILL_DIR / "SKILL.md")
        assert skill.skill_dir == str(self.SKILL_DIR)
        assert skill.steps[0].script == "scripts/analyze.py"
        result = SkillValidator().validate(skill, skill_dir=self.SKILL_DIR)
        assert result.is_valid, result.risks
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_run_with_csv_fixture(self, tmp_path):
        from vibe.harness.skills.parser import SkillParser

        csv_path = self._write_fixture(tmp_path)
        skill = SkillParser().parse_file(self.SKILL_DIR / "SKILL.md")
        tool = SkillRunnerTool({skill.id: skill}, _real_tool_system(tmp_path))

        result = await tool.execute(
            skill_id="stock-analysis", variables={"ticker": "TEST", "csv": str(csv_path)}
        )

        assert result.success, result.error
        data = SkillRunnerTool._extract_json_object(result.content)
        assert data["ticker"] == "TEST"
        assert data["data_points"] == 30
        assert data["sma_20"] == 119.5  # mean of closes 110..129

    @pytest.mark.asyncio
    async def test_run_with_missing_csv_fails(self, tmp_path):
        from vibe.harness.skills.parser import SkillParser

        skill = SkillParser().parse_file(self.SKILL_DIR / "SKILL.md")
        tool = SkillRunnerTool({skill.id: skill}, _real_tool_system(tmp_path))

        result = await tool.execute(
            skill_id="stock-analysis",
            variables={"ticker": "TEST", "csv": str(tmp_path / "nope.csv")},
        )

        assert not result.success

    @pytest.mark.asyncio
    async def test_ticker_pattern_rejects_injection(self, tmp_path):
        from vibe.harness.skills.parser import SkillParser

        csv_path = self._write_fixture(tmp_path)
        skill = SkillParser().parse_file(self.SKILL_DIR / "SKILL.md")
        tool = SkillRunnerTool({skill.id: skill}, _real_tool_system(tmp_path))

        result = await tool.execute(
            skill_id="stock-analysis",
            variables={"ticker": "Q; rm -rf /", "csv": str(csv_path)},
        )

        assert not result.success
        assert "Variable validation failed" in (result.error or "")

    def test_substitute_vars_quotes_defaults_with_spaces(self):
        # When quote=True, a default containing spaces should be safely quoted
        cmd = "python run.py --title ${TITLE:-Default Title With Spaces}"
        res = SkillRunnerTool._substitute_vars(cmd, {}, quote=True)
        assert res == "python run.py --title 'Default Title With Spaces'"
