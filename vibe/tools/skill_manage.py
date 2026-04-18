import os
from pathlib import Path
from typing import Any, Dict

from vibe.tools.tool_system import Tool, ToolResult


class SkillManageTool(Tool):
    """Tool that allows the agent to create / update skills by writing SKILL.md files."""

    def __init__(self, skills_dir: str = "~/.hermes/skills"):
        super().__init__(
            name="skill_manage",
            description=(
                "Create or update a Hermes skill. "
                "Writes a SKILL.md file under ~/.hermes/skills/<skill_name>/."
            ),
        )
        self.skills_dir = Path(skills_dir).expanduser()

    def get_schema(self) -> Dict[str, Any]:
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

        # Path traversal guard
        try:
            resolved = skill_dir.resolve()
            base = self.skills_dir.resolve()
            if not str(resolved).startswith(str(base)):
                return ToolResult(
                    success=False, content=None, error="Path traversal blocked."
                )
        except (OSError, RuntimeError) as e:
            return ToolResult(success=False, content=None, error=f"Path resolution error: {e}")

        if action == "create" and skill_dir.exists():
            return ToolResult(
                success=False,
                content=None,
                error=f"Skill '{name}' already exists. Use action='update' to overwrite.",
            )

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(str(content), encoding="utf-8")
            return ToolResult(
                success=True,
                content=f"Skill '{name}' {action}d at {skill_file}",
            )
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
