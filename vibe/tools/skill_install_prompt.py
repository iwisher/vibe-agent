"""PromptSkillInstallTool — install YAML prompt skills for planner discovery."""

from pathlib import Path
from typing import Any

from vibe.tools.tool_system import Tool, ToolResult


class PromptSkillInstallTool(Tool):
    """Install a prompt skill (YAML frontmatter) for planner discovery.

    Prompt skills provide behavioral guidance to the LLM and are injected
    into the system prompt when the planner matches them. They are NOT
    executable — they guide the LLM's reasoning.
    """

    def __init__(self, skills_dir: Path | str = "~/.vibe/skills"):
        super().__init__(
            name="skill_install_prompt",
            description=(
                "Install a prompt skill from a URL or local path. "
                "Prompt skills are YAML files with behavioral guidance "
                "that the planner injects into the system prompt. "
                "They are NOT executable — they guide the LLM's reasoning."
            ),
        )
        self.skills_dir = Path(skills_dir).expanduser().resolve()

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "URL or local path to the .md file",
                },
                "name": {
                    "type": "string",
                    "description": "Optional: override the skill name (used as filename)",
                },
            },
            "required": ["source"],
        }

    async def execute(self, *, source: str, name: str | None = None, **kwargs) -> ToolResult:
        """Install a prompt skill from source."""
        try:
            content = self._fetch_content(source)
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Failed to fetch skill from {source}: {e}",
            )

        # Validate YAML frontmatter
        if not content.lstrip().startswith("---"):
            return ToolResult(
                success=False,
                content=None,
                error="Invalid prompt skill: must start with YAML frontmatter (---)",
            )

        # Determine filename
        if name:
            filename = f"{name}.md"
        else:
            # Extract name from frontmatter or use basename
            filename = self._extract_name(content) or Path(source).name
            if not filename.endswith(".md"):
                filename = f"{filename}.md"

        # Resolve and jail destination path to skills_dir
        dest = (self.skills_dir / filename).resolve()
        try:
            dest.relative_to(self.skills_dir)
            if dest == self.skills_dir:
                raise ValueError()
        except ValueError:
            return ToolResult(
                success=False,
                content=None,
                error="Security violation: Invalid or unsafe skill name/path.",
            )

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            dest.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Failed to write skill to {dest}: {e}",
            )

        return ToolResult(
            success=True,
            content=f"Prompt skill installed to {dest}",
            error=None,
        )

    def _fetch_content(self, source: str) -> str:
        """Fetch content from URL or local path."""
        if source.startswith("http://") or source.startswith("https://"):
            import urllib.request
            with urllib.request.urlopen(source, timeout=30) as resp:
                return resp.read().decode("utf-8")
        else:
            path = Path(source).expanduser().resolve()
            return path.read_text(encoding="utf-8")

    def _extract_name(self, content: str) -> str | None:
        """Extract skill name from YAML frontmatter."""
        import yaml
        try:
            # Find the --- block
            lines = content.split("\n")
            if not lines or lines[0].strip() != "---":
                return None
            # Collect YAML lines until next --- or end
            yaml_lines = []
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                yaml_lines.append(line)
            data = yaml.safe_load("\n".join(yaml_lines))
            if isinstance(data, dict):
                return data.get("name")
        except Exception:
            pass
        return None
