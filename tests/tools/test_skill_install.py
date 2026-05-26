"""Unit tests for skill_install and skill_list tools and ChatApprovalGate."""

import tempfile
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

import sys
from unittest.mock import MagicMock, patch

from vibe.tools.skill_install import SkillInstallExecutableTool, SkillListTool, ChatApprovalGate
from vibe.tools.tool_system import ToolResult

SAMPLE_SKILL = """+++
vibe_skill_version = "2.0.0"
id = "sample-skill"
name = "Sample Skill"
description = "A sample skill"
category = "test"
tags = ["test"]

[trigger]
patterns = ["sample"]
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "Hello"
tool = "bash"
command = "echo hello"
+++

# Sample Skill
"""


@pytest.mark.asyncio
async def test_skill_install_tool_schema():
    tool = SkillInstallExecutableTool()
    schema = tool.get_schema()
    assert schema["type"] == "object"
    assert "source" in schema["properties"]
    assert "skill_id" in schema["properties"]
    assert "source" in schema["required"]


@pytest.mark.asyncio
async def test_skill_install_tool_missing_source():
    tool = SkillInstallExecutableTool()
    result = await tool.execute()
    assert not result.success
    assert "Missing required parameter: 'source'" in result.error


@pytest.mark.asyncio
async def test_skill_install_tool_local_path_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # Create a source skill directory
        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        tool = SkillInstallExecutableTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir))

        assert result.success
        assert isinstance(result.content, str)
        assert "sample-skill" in result.content
        assert "Sample Skill" in result.content
        assert "A sample skill" in result.content
        assert (skills_dir / "sample-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_skill_install_tool_skill_id_override():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        tool = SkillInstallExecutableTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir), skill_id="custom-id")

        assert result.success
        assert isinstance(result.content, str)
        assert "custom-id" in result.content
        assert (skills_dir / "custom-id" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_skill_install_tool_invalid_skill_id_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        tool = SkillInstallExecutableTool(skills_dir=skills_dir)
        result = await tool.execute(source="/nonexistent", skill_id="../../../etc/passwd")

        assert not result.success
        assert result.error is not None
        assert "Invalid skill_id" in result.error
        assert "alphanumeric" in result.error


@pytest.mark.asyncio
async def test_skill_install_tool_local_path_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        tool = SkillInstallExecutableTool(skills_dir=skills_dir)
        result = await tool.execute(source="/nonexistent/path/to/skill")
        assert not result.success
        assert "Path not found" in result.error


@pytest.mark.asyncio
async def test_skill_list_tool_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        tool = SkillListTool(skills_dir=skills_dir)
        result = await tool.execute()
        assert result.success
        assert isinstance(result.content, str)
        assert "No skills installed" in result.content


@pytest.mark.asyncio
async def test_skill_list_tool_with_skills():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = (tmp_path / "skills").resolve()
        skills_dir.mkdir()

        # Pre-install a skill by using SkillInstallExecutableTool
        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        install_tool = SkillInstallExecutableTool(skills_dir=skills_dir)
        install_res = await install_tool.execute(source=str(source_dir))
        assert install_res.success

        list_tool = SkillListTool(skills_dir=skills_dir)
        result = await list_tool.execute()
        assert result.success
        assert isinstance(result.content, str)
        assert "Installed skills: 1" in result.content
        assert "sample-skill" in result.content
        assert "2.0.0" in result.content


def test_chat_approval_gate_blocks_risks():
    gate = ChatApprovalGate()
    # risks present
    assert gate.approve("Sample", risks=["critical vulnerability"], warnings=[]) is False
    assert gate.approve("Sample", risks=["critical vulnerability"], warnings=["some warning"]) is False


def test_chat_approval_gate_approves_warnings():
    gate = ChatApprovalGate()
    # warnings present, no risks
    assert gate.approve("Sample", risks=[], warnings=["warning one", "warning two"]) is True
    # nothing present
    assert gate.approve("Sample", risks=[], warnings=[]) is True


@pytest.mark.asyncio
async def test_format_result_includes_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        tool = SkillInstallExecutableTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir))

        assert result.success
        assert isinstance(result.content, str)
        assert "Sample Skill" in result.content
        assert "A sample skill" in result.content
        assert "2.0.0" in result.content
        assert "test" in result.content
        assert "Steps: 1" in result.content


def test_chat_approval_gate_interactive_disabled_by_default():
    """With interactive_skill_install disabled (default), risks are always blocked."""
    gate = ChatApprovalGate()
    mock_config = MagicMock()
    mock_config.security.interactive_skill_install = False

    with patch("vibe.core.config.VibeConfig.load", return_value=mock_config):
        result = gate.approve("RiskySkill", risks=["critical vulnerability"], warnings=[])
        assert result is False


def test_chat_approval_gate_interactive_approves_on_y():
    """When interactive is enabled and user types 'y', approve the risk."""
    gate = ChatApprovalGate()
    mock_config = MagicMock()
    mock_config.security.interactive_skill_install = True

    with patch("vibe.core.config.VibeConfig.load", return_value=mock_config):
        with patch("sys.stdin.isatty", return_value=True):
            with patch("sys.stdout.flush"):
                with patch("select.select", return_value=([sys.stdin], [], [])):
                    with patch("sys.stdin.readline", return_value="y\n"):
                        result = gate.approve("RiskySkill", risks=["critical"], warnings=[])
                        assert result is True


def test_chat_approval_gate_interactive_rejects_on_n():
    """When interactive is enabled and user types 'n', reject the risk."""
    gate = ChatApprovalGate()
    mock_config = MagicMock()
    mock_config.security.interactive_skill_install = True

    with patch("vibe.core.config.VibeConfig.load", return_value=mock_config):
        with patch("sys.stdin.isatty", return_value=True):
            with patch("sys.stdout.flush"):
                with patch("select.select", return_value=([sys.stdin], [], [])):
                    with patch("sys.stdin.readline", return_value="n\n"):
                        result = gate.approve("RiskySkill", risks=["critical"], warnings=[])
                        assert result is False


def test_chat_approval_gate_interactive_rejects_on_timeout(capsys):
    """When interactive prompt times out, reject and print timeout message."""
    gate = ChatApprovalGate()
    mock_config = MagicMock()
    mock_config.security.interactive_skill_install = True

    with patch("vibe.core.config.VibeConfig.load", return_value=mock_config):
        with patch("sys.stdin.isatty", return_value=True):
            with patch("sys.stdout.flush"):
                with patch("select.select", return_value=([], [], [])):
                    result = gate.approve("RiskySkill", risks=["critical"], warnings=[])
                    assert result is False

    captured = capsys.readouterr()
    assert "Timeout" in captured.out or "Auto-rejecting" in captured.out


def test_chat_approval_gate_non_tty_blocks():
    """When stdin is not a TTY, interactive prompt is skipped and risk is blocked."""
    gate = ChatApprovalGate()
    mock_config = MagicMock()
    mock_config.security.interactive_skill_install = True

    with patch("vibe.core.config.VibeConfig.load", return_value=mock_config):
        with patch("sys.stdin.isatty", return_value=False):
            result = gate.approve("RiskySkill", risks=["critical"], warnings=[])
            assert result is False


def test_chat_approval_gate_prompt_timeout_fallback():
    """_prompt_with_timeout returns None when select.select times out."""
    gate = ChatApprovalGate()
    with patch("select.select", return_value=([], [], [])):
        result = gate._prompt_with_timeout("test: ", 0.1)
        assert result is None


def test_chat_approval_gate_prompt_reads_input():
    """_prompt_with_timeout returns stripped user input on success."""
    gate = ChatApprovalGate()
    with patch("select.select", return_value=([sys.stdin], [], [])):
        with patch("sys.stdin.readline", return_value="yes\n"):
            result = gate._prompt_with_timeout("test: ", 1.0)
            assert result == "yes"


def test_chat_approval_gate_prompt_windows_fallback():
    """On Windows (OSError from select), _prompt_with_timeout falls back to threading."""
    gate = ChatApprovalGate()

    def _raise_oserror(*args, **kwargs):
        raise OSError("select does not support stdin on Windows")

    with patch("select.select", side_effect=_raise_oserror):
        with patch("sys.stdin.readline", return_value="y\n"):
            result = gate._prompt_with_timeout("test: ", 1.0)
            assert result == "y"
