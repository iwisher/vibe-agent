"""Tests for InstructionLoader unified discovery (YAML + TOML)."""
import pytest
from pathlib import Path

from vibe.harness.instructions import InstructionLoader, Skill


class TestUnifiedDiscovery:
    def test_detect_format_yaml(self):
        text = "---\nname: test\n---\n\ncontent"
        assert InstructionLoader._detect_format(text) == "yaml"

    def test_detect_format_toml(self):
        text = "+++\nid = \"test\"\n+++\n\ncontent"
        assert InstructionLoader._detect_format(text) == "toml"

    def test_detect_format_unknown(self):
        text = "# Just markdown"
        assert InstructionLoader._detect_format(text) == "unknown"

    def test_detect_format_with_leading_whitespace(self):
        text = "\n\n---\nname: test\n---\n"
        assert InstructionLoader._detect_format(text) == "yaml"

    def test_scan_skill_files_flat_and_nested(self, tmp_path):
        # Flat layout
        (tmp_path / "flat.md").write_text("---\nname: flat\n---\n")
        # Nested layout
        nested = tmp_path / "category" / "nested"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("+++\nid = \"nested\"\n+++\n")

        files = InstructionLoader._scan_skill_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert "flat.md" in names
        assert "SKILL.md" in names

    def test_load_unified_mixed_formats(self, tmp_path):
        # YAML prompt skill
        (tmp_path / "prompt.md").write_text("""---
name: Prompt Skill
description: A prompt skill
---

# Prompt Skill

This is a prompt skill.
""")
        # TOML executable skill
        nested = tmp_path / "exec"
        nested.mkdir()
        (nested / "SKILL.md").write_text("""+++
vibe_skill_version = "2.0.0"
id = "exec-skill"
name = "Exec Skill"
description = "An exec skill"

[[steps]]
id = "step1"
description = "Step 1"
tool = "bash"
command = "echo hello"
+++
""")

        loader = InstructionLoader(skills_dir=str(tmp_path))
        prompt_skills, executable_skills = loader.load_unified()

        assert len(prompt_skills) == 1
        assert prompt_skills[0].name == "Prompt Skill"
        assert "prompt skill" in prompt_skills[0].content.lower()

        assert len(executable_skills) == 1
        assert "exec-skill" in executable_skills
        assert executable_skills["exec-skill"].name == "Exec Skill"

    def test_load_unified_skips_malformed_toml(self, tmp_path):
        (tmp_path / "bad.md").write_text("+++\nnot valid toml\n+++")
        loader = InstructionLoader(skills_dir=str(tmp_path))
        prompt_skills, executable_skills = loader.load_unified()
        assert len(prompt_skills) == 0
        assert len(executable_skills) == 0

    def test_load_backward_compat(self, tmp_path):
        (tmp_path / "skill.md").write_text("""---
name: Backward Compat
description: Test
---

Content here.
""")
        loader = InstructionLoader(skills_dir=str(tmp_path))
        instruction_set = loader.load()
        assert len(instruction_set.skills) == 1
        assert instruction_set.skills[0].name == "Backward Compat"
