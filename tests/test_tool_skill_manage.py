"""Test skill manage tool."""
import tempfile
from pathlib import Path

import pytest

from vibe.tools.skill_manage import SkillManageTool


@pytest.mark.asyncio
async def test_create_skill_writes_skill_md():
    with tempfile.TemporaryDirectory() as tmp:
        tool = SkillManageTool(skills_dir=tmp)
        content = """+++
vibe_skill_version = "2.0.0"
id = "test-skill"
name = "Test"
description = "Test"
category = "test"
tags = ["test"]

[trigger]
patterns = ["test"]
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "Hello"
tool = "bash"
command = "echo hello"
+++

# Test
"""
        result = await tool.execute(action="create", name="test-skill", content=content)
        assert result.success
        assert (Path(tmp) / "test-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_create_skill_validates_content():
    with tempfile.TemporaryDirectory() as tmp:
        tool = SkillManageTool(skills_dir=tmp)
        # Missing frontmatter — should fail validation
        result = await tool.execute(action="create", name="bad", content="# No frontmatter\nJust markdown")
        assert not result.success
        assert "Invalid" in result.error
