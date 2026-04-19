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

        try:
            resolved.mkdir(parents=True, exist_ok=True)
            skill_file = resolved / "SKILL.md"
            skill_file.write_text(str(content), encoding="utf-8")
            return ToolResult(
                success=True,
                content=f"Skill '{name}' {action}d at {skill_file}",
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
