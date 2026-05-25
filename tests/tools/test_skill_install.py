"""Unit tests for skill_install and skill_list tools and ChatApprovalGate."""

import tempfile
from pathlib import Path
import pytest

from vibe.tools.skill_install import SkillInstallTool, SkillListTool, ChatApprovalGate
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
    tool = SkillInstallTool()
    schema = tool.get_schema()
    assert schema["type"] == "object"
    assert "source" in schema["properties"]
    assert "skill_id" in schema["properties"]
    assert "source" in schema["required"]


@pytest.mark.asyncio
async def test_skill_install_tool_missing_source():
    tool = SkillInstallTool()
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

        tool = SkillInstallTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir))

        assert result.success
        assert result.content["skill_id"] == "sample-skill"
        assert result.content["name"] == "Sample Skill"
        assert result.content["description"] == "A sample skill"
        assert result.content["version"] == "2.0.0"
        assert result.content["category"] == "test"
        assert result.content["tags"] == ["test"]
        assert result.content["steps_count"] == 1
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

        tool = SkillInstallTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir), skill_id="custom-id")

        assert result.success
        assert result.content["skill_id"] == "custom-id"
        assert (skills_dir / "custom-id" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_skill_install_tool_invalid_skill_id_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        tool = SkillInstallTool(skills_dir=skills_dir)
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

        tool = SkillInstallTool(skills_dir=skills_dir)
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
        assert result.content["count"] == 0
        assert result.content["skills"] == []


@pytest.mark.asyncio
async def test_skill_list_tool_with_skills():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = (tmp_path / "skills").resolve()
        skills_dir.mkdir()

        # Pre-install a skill by using SkillInstallTool
        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        install_tool = SkillInstallTool(skills_dir=skills_dir)
        install_res = await install_tool.execute(source=str(source_dir))
        assert install_res.success

        list_tool = SkillListTool(skills_dir=skills_dir)
        result = await list_tool.execute()
        assert result.success
        assert result.content["count"] == 1
        skill_info = result.content["skills"][0]
        assert skill_info["id"] == "sample-skill"
        assert skill_info["version"] == "2.0.0"
        assert "installed_at" in skill_info
        assert skill_info["path"] == str(skills_dir / "sample-skill")


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

        tool = SkillInstallTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir))

        assert result.success
        assert "name" in result.content
        assert "description" in result.content
        assert "version" in result.content
        assert "category" in result.content
        assert "tags" in result.content
        assert "steps_count" in result.content
        assert "variables" in result.content
