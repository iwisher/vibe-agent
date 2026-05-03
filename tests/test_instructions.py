"""Tests for vibe.harness.instructions."""


from vibe.harness.instructions import InstructionLoader, InstructionSet, Skill


def test_build_system_prompt_basic():
    inst = InstructionSet(
        global_agents="Global rule",
        project_agents="Project rule",
    )
    prompt = inst.build_system_prompt()
    assert "Global rule" in prompt
    assert "Project rule" in prompt


def test_build_system_prompt_with_auto_load_skills():
    inst = InstructionSet(
        global_agents="Global",
        skills=[
            Skill(name="s1", description="d1", content="c1", auto_load=True),
            Skill(name="s2", description="d2", content="c2", auto_load=False),
        ],
    )
    prompt = inst.build_system_prompt()
    assert "s1" in prompt
    assert "c1" in prompt
    assert "s2" not in prompt


def test_build_system_prompt_explicit_include():
    inst = InstructionSet(
        skills=[
            Skill(name="s1", description="d1", content="c1", auto_load=False),
        ],
    )
    prompt = inst.build_system_prompt(include_skills=["s1"])
    assert "s1" in prompt


def test_loader_reads_agents(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    vibe_dir = home / ".vibe"
    vibe_dir.mkdir()
    (vibe_dir / "AGENTS.md").write_text("Global AGENTS")

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "AGENTS.md").write_text("Project AGENTS")

    loader = InstructionLoader(
        global_agents_path=vibe_dir / "AGENTS.md",
        project_agents_path=proj_dir / "AGENTS.md",
        skills_dir=proj_dir / "skills",
    )
    result = loader.load()
    assert result.global_agents == "Global AGENTS"
    assert result.project_agents == "Project AGENTS"


def test_loader_parses_skills_with_frontmatter(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "python.md"
    skill_file.write_text(
        "---\n"
        "name: python-expert\n"
        "description: Python best practices\n"
        "auto_load: true\n"
        "tags: [coding, python]\n"
        "---\n"
        "Always use type hints.\n"
    )

    loader = InstructionLoader(skills_dir=skills_dir)
    result = loader.load()
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.name == "python-expert"
    assert skill.description == "Python best practices"
    assert skill.auto_load is True
    assert "type hints" in skill.content


def test_loader_handles_missing_files(tmp_path):
    loader = InstructionLoader(
        global_agents_path=tmp_path / "missing.md",
        project_agents_path=tmp_path / "also_missing.md",
        skills_dir=tmp_path / "no_skills",
    )
    result = loader.load()
    assert result.global_agents == ""
    assert result.project_agents == ""
    assert result.skills == []


def test_parse_frontmatter_no_delimiter():
    text = "Just markdown content"
    fm, content = InstructionLoader._parse_frontmatter(text)
    assert fm == {}
    assert content == text
