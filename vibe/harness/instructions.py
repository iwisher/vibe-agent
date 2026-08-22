"""Instruction loader for AGENTS.md and skills."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    name: str
    description: str
    content: str
    auto_load: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class InstructionSet:
    global_agents: str = ""
    project_agents: str = ""
    skills: list[Skill] = field(default_factory=list)

    def build_system_prompt(self, include_skills: list[str] | None = None) -> str:
        """Build the full system prompt from AGENTS.md and skills."""
        parts = []

        # Add mandatory environment constraints
        parts.append(
            "# Environment Constraints\n"
            "You are operating in a restricted environment for security and stability.\n"
            "- **Tool Usage**: Use only one tool at a time unless you are certain they are "
            "independent.\n"
            "- **Bash Constraints**: The `bash` tool only supports simple commands. "
            "Pipes (|), redirects (>, >>), command chaining (&&, ;), and variable expansion ($) "
            "are strictly forbidden. If you need to process or redirect output, do it across "
            "multiple turns or use specialized tools like `write_file`."
        )

        if self.global_agents:
            parts.append(self.global_agents)
        if self.project_agents:
            parts.append(self.project_agents)

        active_skills = []
        for skill in self.skills:
            if skill.auto_load or (include_skills and skill.name in include_skills):
                active_skills.append(skill)

        if active_skills:
            parts.append("\n\n## Skills")
            for skill in active_skills:
                parts.append(f"\n### {skill.name}\n{skill.description}\n{skill.content}")

        return "\n\n".join(parts).strip()


class InstructionLoader:
    """Loads AGENTS.md and skills into an InstructionSet."""

    def __init__(
        self,
        global_agents_path: str | None = None,
        project_agents_path: str | None = None,
        skills_dir: str | None = None,
        skills_dirs: list[str] | None = None,
    ):
        self.global_agents_path = Path(global_agents_path or Path.home() / ".vibe" / "AGENTS.md")
        self.project_agents_path = Path(project_agents_path or "./AGENTS.md")
        self.skills_dir = Path(skills_dir or Path.home() / ".vibe" / "skills")
        self.skills_dirs = skills_dirs or []

    def load(self) -> InstructionSet:
        return InstructionSet(
            global_agents=self._read_file(self.global_agents_path),
            project_agents=self._read_file(self.project_agents_path),
            skills=self._load_skills(),
        )

    def load_unified(self) -> tuple[list[Skill], dict[str, Any]]:
        """Load both prompt skills (YAML) and executable skills (TOML).

        Returns:
            (prompt_skills, executable_skills) where:
            - prompt_skills: list[Skill] for system prompt injection
            - executable_skills: dict[str, Any] for SkillRunnerTool
        """
        from vibe.harness.skills.parser import SkillParser

        prompt_skills: list[Skill] = []
        executable_skills: dict[str, Any] = {}

        all_dirs = [self.skills_dir] + [Path(d) for d in self.skills_dirs]

        for base_dir in all_dirs:
            if not base_dir.exists():
                continue

            for file_path in self._scan_skill_files(base_dir):
                text = file_path.read_text(encoding="utf-8")
                fmt = self._detect_format(text)

                if fmt == "yaml":
                    # YAML → prompt skill (v1)
                    frontmatter, content = self._parse_frontmatter(text)
                    prompt_skills.append(
                        Skill(
                            name=frontmatter.get("name", file_path.stem),
                            description=frontmatter.get("description", ""),
                            content=content.strip(),
                            auto_load=bool(frontmatter.get("auto_load", False)),
                            tags=frontmatter.get("tags", []) or [],
                        )
                    )
                elif fmt == "toml":
                    # TOML → executable skill (v2)
                    try:
                        parser = SkillParser()
                        skill = parser.parse_string(text)
                        # Record source dir so script steps can resolve scripts/...
                        skill.skill_dir = str(file_path.parent)
                        executable_skills[skill.id] = skill
                    except Exception:
                        # Skip malformed TOML skills
                        continue

        return prompt_skills, executable_skills

    @staticmethod
    def _scan_skill_files(base_dir: Path) -> set[Path]:
        """Scan for skill files: flat *.md and nested */SKILL.md."""
        files: set[Path] = set()
        # Flat layout: ~/.vibe/skills/*.md
        files.update(base_dir.glob("*.md"))
        # Nested layout: ~/.vibe/skills/<category>/<skill>/SKILL.md
        files.update(base_dir.rglob("SKILL.md"))
        return files

    @staticmethod
    def _detect_format(text: str) -> str:
        """Detect skill format by frontmatter delimiter."""
        stripped = text.lstrip()
        if stripped.startswith("+++"):
            return "toml"
        elif stripped.startswith("---"):
            return "yaml"
        return "unknown"

    @staticmethod
    def _read_file(path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def _load_skills(self) -> list[Skill]:
        skills = []
        if not self.skills_dir.exists():
            return skills

        for file in sorted(self.skills_dir.glob("*.md")):
            text = file.read_text(encoding="utf-8")
            frontmatter, content = self._parse_frontmatter(text)
            skills.append(
                Skill(
                    name=frontmatter.get("name", file.stem),
                    description=frontmatter.get("description", ""),
                    content=content.strip(),
                    auto_load=bool(frontmatter.get("auto_load", False)),
                    tags=frontmatter.get("tags", []) or [],
                )
            )
        return skills

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple:
        """Parse YAML frontmatter from markdown text.

        Supports both --- delimited frontmatter and raw markdown.
        """
        if not text.lstrip().startswith("---"):
            return {}, text

        try:
            parts = text.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                content = parts[2]
                return frontmatter, content
        except Exception:
            pass

        return {}, text
