"""Phase 1 security tests for file tools and skill management.

Covers:
- Symlink escape blocking in ReadFileTool / WriteFileTool
- Path traversal blocking in SkillManageTool
- MCPServerConfig mutable default isolation
"""

import os
from pathlib import Path

import pytest

from vibe.tools.file import ReadFileTool, WriteFileTool
from vibe.tools.skill_manage import SkillManageTool
from vibe.tools.mcp_bridge import MCPServerConfig


# ---------------------------------------------------------------------------
# File tool symlink escape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_001_symlink_escape_blocked(tmp_path):
    """A symlink inside root_dir pointing outside must be blocked."""
    # Create jail and a symlink inside it pointing outside
    jail = tmp_path / "jail"
    jail.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("secret")
    symlink = jail / "link_to_secret"
    symlink.symlink_to(target)

    tool = ReadFileTool(root_dir=str(jail))
    result = await tool.execute(path=str(symlink))
    assert not result.success
    assert "escapes" in result.error.lower()


@pytest.mark.asyncio
async def test_file_002_dotdot_escape_blocked(tmp_path):
    """Path traversal via .. components must be blocked."""
    jail = tmp_path / "jail"
    jail.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")

    tool = ReadFileTool(root_dir=str(jail))
    result = await tool.execute(path="../secret.txt")
    assert not result.success
    assert "escapes" in result.error.lower()


@pytest.mark.asyncio
async def test_file_003_symlink_inside_jail_allowed(tmp_path):
    """A symlink inside root_dir pointing to another file inside jail is OK."""
    jail = tmp_path / "jail"
    jail.mkdir()
    real_file = jail / "real.txt"
    real_file.write_text("hello")
    symlink = jail / "link_to_real"
    symlink.symlink_to(real_file)

    tool = ReadFileTool(root_dir=str(jail))
    result = await tool.execute(path=str(symlink))
    assert result.success
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_file_004_write_symlink_escape_blocked(tmp_path):
    """WriteFileTool must also block symlink escapes."""
    jail = tmp_path / "jail"
    jail.mkdir()
    target = tmp_path / "passwd"
    target.write_text("original")
    symlink = jail / "link_to_passwd"
    symlink.symlink_to(target)

    tool = WriteFileTool(root_dir=str(jail))
    result = await tool.execute(path=str(symlink), content="hacked")
    assert not result.success
    assert "escapes" in result.error.lower()
    # Ensure target was NOT overwritten
    assert target.read_text() == "original"


# ---------------------------------------------------------------------------
# SkillManageTool path traversal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skill_001_path_traversal_blocked(tmp_path):
    """Skill names containing .. must be blocked."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="../evil", content="x")
    assert not result.success
    assert "traversal" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_002_category_traversal_blocked(tmp_path):
    """Category names containing .. must be blocked."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="good", category="../evil", content="x")
    assert not result.success
    assert "traversal" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_003_absolute_name_blocked(tmp_path):
    """An absolute path as skill name must be blocked."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="/etc/passwd", content="x")
    assert not result.success
    assert "traversal" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_004_valid_skill_allowed(tmp_path):
    """Normal skill creation inside the jail should succeed."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="my-skill", content="# Hello")
    assert result.success
    assert (tmp_path / "skills" / "my-skill" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# MCPServerConfig mutable defaults
# ---------------------------------------------------------------------------

def test_mcp_001_mutable_defaults_isolated():
    """Mutating one config's args must not affect another config."""
    cfg1 = MCPServerConfig(name="a", description="A")
    cfg2 = MCPServerConfig(name="b", description="B")
    cfg1.args.append("--foo")
    assert "--foo" not in cfg2.args
    cfg1.tools.append({"name": "t1"})
    assert len(cfg2.tools) == 0
