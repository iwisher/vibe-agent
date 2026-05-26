"""Tests for PromptSkillInstallTool."""

import pytest

from vibe.tools.skill_install_prompt import PromptSkillInstallTool


class TestPromptSkillInstallTool:
    """Test PromptSkillInstallTool functionality."""

    @pytest.mark.asyncio
    async def test_install_from_local_path(self, tmp_path):
        tool = PromptSkillInstallTool(skills_dir=tmp_path)

        # Create a source YAML skill
        source = tmp_path / "source_skill.md"
        source.write_text("---\nname: test-skill\n---\n# Test Skill\n")

        result = await tool.execute(source=str(source))

        assert result.success is True
        assert "test-skill.md" in result.content
        assert (tmp_path / "test-skill.md").exists()

    @pytest.mark.asyncio
    async def test_install_with_name_override(self, tmp_path):
        tool = PromptSkillInstallTool(skills_dir=tmp_path)

        source = tmp_path / "source.md"
        source.write_text("---\nname: original\n---\n# Original\n")

        result = await tool.execute(source=str(source), name="overridden")

        assert result.success is True
        assert (tmp_path / "overridden.md").exists()

    @pytest.mark.asyncio
    async def test_rejects_non_yaml(self, tmp_path):
        tool = PromptSkillInstallTool(skills_dir=tmp_path)

        source = tmp_path / "bad.md"
        source.write_text("# No frontmatter\nJust markdown\n")

        result = await tool.execute(source=str(source))

        assert result.success is False
        assert result.error is not None
        assert "YAML frontmatter" in result.error

    @pytest.mark.asyncio
    async def test_extracts_name_from_frontmatter(self, tmp_path):
        tool = PromptSkillInstallTool(skills_dir=tmp_path)

        source = tmp_path / "unnamed.md"
        source.write_text("---\nname: extracted-name\n---\n# Content\n")

        result = await tool.execute(source=str(source))

        assert result.success is True
        assert (tmp_path / "extracted-name.md").exists()

    @pytest.mark.asyncio
    async def test_prevent_path_traversal(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        tool = PromptSkillInstallTool(skills_dir=skills_dir)

        # 1. Traversal via overridden name
        source = tmp_path / "traversal.md"
        source.write_text("---\nname: original\n---\n# Content\n")

        result = await tool.execute(source=str(source), name="../../hack")
        assert result.success is False
        assert "Security violation" in result.error
        assert not (tmp_path / "hack.md").exists()

        # 2. Traversal via extracted frontmatter name
        source2 = tmp_path / "traversal2.md"
        source2.write_text("---\nname: ../../hack2\n---\n# Content\n")

        result2 = await tool.execute(source=str(source2))
        assert result2.success is False
        assert "Security violation" in result2.error
        assert not (tmp_path / "hack2.md").exists()
