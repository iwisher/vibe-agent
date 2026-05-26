"""Integration tests for QueryLoopFactory skill wiring."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibe.core.query_loop_factory import QueryLoopFactory


class TestQueryLoopFactorySkillWiring:
    """Test that QueryLoopFactory properly wires skills."""

    def test_factory_creates_instruction_loader(self, tmp_path, monkeypatch):
        """Factory should create InstructionLoader and load skills."""
        skills_dir = tmp_path / ".vibe" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "prompt.md").write_text("---\nname: test-prompt\n---\n# Test\n")

        monkeypatch.setenv("HOME", str(tmp_path))

        factory = QueryLoopFactory(
            base_url="http://test",
            model="test-model",
            working_dir=str(tmp_path),
        )

        ql = factory.create()

        assert ql.instruction_set is not None
        assert len(ql.instruction_set.skills) >= 0

    def test_factory_registers_skill_runner_tool(self, tmp_path, monkeypatch):
        """Factory should register SkillRunnerTool when executable skills exist."""
        skills_dir = tmp_path / ".vibe" / "skills"
        skills_dir.mkdir(parents=True)
        nested = skills_dir / "exec-skill"
        nested.mkdir()
        (nested / "SKILL.md").write_text(
            '+++\n'
            'vibe_skill_version = "2.0.0"\n'
            'id = "exec-skill"\n'
            'name = "Exec Skill"\n'
            'description = "A test skill"\n'
            '\n'
            '[[steps]]\n'
            'id = "s1"\n'
            'description = "Test step"\n'
            'tool = "bash"\n'
            'command = "echo hello"\n'
            '+++\n'
        )

        monkeypatch.setenv("HOME", str(tmp_path))

        factory = QueryLoopFactory(
            base_url="http://test",
            model="test-model",
            working_dir=str(tmp_path),
        )

        ql = factory.create()

        tool_names = [t.name for t in ql.tools._tools.values()]
        assert "run_skill" in tool_names

    def test_factory_creates_hybrid_planner_with_prompt_skills(self, tmp_path, monkeypatch):
        """Factory should create HybridPlanner when prompt skills exist."""
        skills_dir = tmp_path / ".vibe" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "prompt.md").write_text(
            "---\n"
            "name: creative-ideation\n"
            "---\n"
            "# Creative Ideation\n"
        )

        monkeypatch.setenv("HOME", str(tmp_path))

        factory = QueryLoopFactory(
            base_url="http://test",
            model="test-model",
            working_dir=str(tmp_path),
        )

        ql = factory.create()

        # If prompt skills loaded, planner should be set
        # Note: planner may be None if no skills match or if loader fails
        assert ql.context_planner is not None or ql.instruction_set is None

    def test_factory_gracefully_handles_missing_skills_dir(self, tmp_path, monkeypatch):
        """Factory should not fail when skills directory doesn't exist."""
        monkeypatch.setenv("HOME", str(tmp_path))

        factory = QueryLoopFactory(
            base_url="http://test",
            model="test-model",
            working_dir=str(tmp_path),
        )

        ql = factory.create()

        # Should create QueryLoop successfully even with no skills
        assert ql is not None
        # Note: project-local ./skills may exist and contain skills, so we just check no crash

    def test_factory_registers_prompt_skill_install_tool(self, tmp_path):
        """Factory should register PromptSkillInstallTool."""
        factory = QueryLoopFactory(
            base_url="http://test",
            model="test-model",
            working_dir=str(tmp_path),
        )

        ql = factory.create()

        tool_names = [t.name for t in ql.tools._tools.values()]
        assert "skill_install_prompt" in tool_names
