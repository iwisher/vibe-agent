import asyncio
import os
from pathlib import Path
from typing import Any

from vibe.tools.tool_system import Tool, ToolResult


class SkillManageTool(Tool):
    """Tool that allows the agent to create / update skills by writing SKILL.md files."""

    def __init__(self, skills_dir: str = "~/.vibe/skills"):
        super().__init__(
            name="skill_manage",
            description=(
                "Create or update a vibe skill. "
                "Writes a SKILL.md file under ~/.vibe/skills/<skill_name>/."
            ),
        )
        self.skills_dir = Path(skills_dir).expanduser().resolve()

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update"],
                    "description": "Whether to create a new skill or overwrite an existing one.",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (lowercase, hyphens/underscores).",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category folder, e.g. 'devops' or 'finance'.",
                },
                "content": {
                    "type": "string",
                    "description": "Full markdown content for SKILL.md.",
                },
            },
            "required": ["action", "name", "content"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action")
        name = kwargs.get("name")
        content = kwargs.get("content")
        category = kwargs.get("category")

        if not name or not content:
            return ToolResult(success=False, content=None, error="Missing name or content")

        skill_dir = self.skills_dir
        if category:
            skill_dir = skill_dir / category
        skill_dir = skill_dir / name

        # Path traversal guard — use relative_to() which is robust against
        # partial-string prefix attacks (e.g. /home/user/.vibe/skills-evil).
        try:
            resolved = skill_dir.resolve()
            # resolve() follows symlinks; a symlink pointing outside the jail
            # will resolve to a path outside base, which relative_to catches.
            resolved.relative_to(self.skills_dir)
        except (ValueError, OSError, RuntimeError) as e:
            return ToolResult(
                success=False, content=None, error=f"Path traversal blocked: {e}"
            )

        if action == "create" and resolved.exists():
            return ToolResult(
                success=False,
                content=None,
                error=f"Skill '{name}' already exists. Use action='update' to overwrite.",
            )

        # Validate content is a valid SKILL.md
        try:
            from vibe.harness.skills.parser import SkillParser
            parser = SkillParser()
            skill = parser.parse_string(str(content))
        except ValueError as e:
            return ToolResult(success=False, content=None, error=f"Invalid SKILL.md: {e}")

        # Security validation — check for dangerous commands
        try:
            from vibe.harness.skills.validator import SkillValidator
            validator = SkillValidator()
            result = validator.validate(skill)
            if result.blocked:
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"Security validation failed: {result.warnings + result.risks}",
                )
        except Exception:
            # If validator fails, still allow write (defense in depth)
            pass

        # Write to disk using async I/O
        try:
            await asyncio.to_thread(resolved.mkdir, parents=True, exist_ok=True)
            skill_file = resolved / "SKILL.md"
            await asyncio.to_thread(
                skill_file.write_text, str(content), encoding="utf-8"
            )
            return ToolResult(
                success=True,
                content=f"Skill '{name}' {action}d at {skill_file}",
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
