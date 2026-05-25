"""Tool for installing vibe skills interactively during chat sessions."""

import re
from pathlib import Path
from typing import Any

from vibe.harness.skills.approval import ApprovalGate
from vibe.harness.skills.installer import InstallResult, SkillInstaller
from vibe.harness.skills.parser import SkillParser

from .tool_system import Tool, ToolResult

# Same regex as Skill.id validation in models.py
_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class ChatApprovalGate(ApprovalGate):
    """Approval gate for chat contexts: auto-approve warnings, auto-reject risks.

    In an interactive chat session, the LLM has already decided to attempt
    installation based on the user's request. We auto-approve warnings
    (non-critical issues) but still block on critical risks (e.g. rm -rf /).
    The user can always uninstall later if needed.
    """

    def approve(
        self,
        skill_name: str,
        risks: list[str],
        warnings: list[str],
    ) -> bool:
        # Block on critical risks regardless
        if risks:
            return False
        # Auto-approve warnings in chat context
        return True


class SkillInstallTool(Tool):
    """Install a vibe skill from git, local path, or tarball URL.

    This tool enables interactive skill installation during chat sessions.
    The user can say "install skill from https://github.com/..." and the
    agent will fetch, validate, and install it automatically.
    """

    def __init__(
        self,
        skills_dir: Path | str = "~/.vibe/skills",
        approval_gate: ApprovalGate | None = None,
    ):
        super().__init__(
            name="skill_install",
            description=(
                "Install a vibe skill from a git repository URL, local directory path, "
                "or tarball URL. The skill will be fetched, validated for security, "
                "and installed into the local skills directory. "
                "Returns the installed skill's metadata on success."
            ),
        )
        self.skills_dir = Path(skills_dir).expanduser().resolve()
        self.installer = SkillInstaller(
            skills_dir=self.skills_dir,
            approval_gate=approval_gate or ChatApprovalGate(),
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Source to install from: a git URL (https://github.com/user/repo), "
                        "a local directory path, or a tarball URL ending in .tar.gz/.tgz"
                    ),
                },
                "skill_id": {
                    "type": "string",
                    "description": "Optional: override the skill ID from the skill's frontmatter",
                },
            },
            "required": ["source"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        source = kwargs.get("source")
        skill_id = kwargs.get("skill_id")
        if not source:
            return ToolResult(
                success=False,
                content=None,
                error="Missing required parameter: 'source'",
            )
        if skill_id is not None and not _SKILL_ID_RE.match(skill_id):
            return ToolResult(
                success=False,
                content=None,
                error=(
                    f"Invalid skill_id '{skill_id}'. "
                    "Must contain only alphanumeric characters, hyphens, and underscores."
                ),
            )
        try:
            result = await self._install(source, skill_id)
            return self._format_result(result)
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Unexpected error during skill installation: {e}",
            )

    async def _install(self, source: str, skill_id: str | None = None) -> InstallResult:
        """Route to the appropriate installer based on source type."""
        if source.startswith("http") and (source.endswith(".tar.gz") or source.endswith(".tgz")):
            return await self.installer.install_from_tarball(source, skill_id)
        elif source.startswith("http") or source.startswith("git@") or source.endswith(".git"):
            return await self.installer.install_from_git(source, skill_id)
        else:
            path = Path(source).expanduser().resolve()
            if not path.exists():
                return InstallResult(
                    success=False,
                    message=f"Path not found: {path}",
                )
            return await self.installer.install_from_path(path, skill_id)

    def _format_result(self, result: InstallResult) -> ToolResult:
        """Format an InstallResult into a user-friendly ToolResult."""
        if not result.success:
            return ToolResult(
                success=False,
                content=None,
                error=result.message,
            )

        # Build a rich response with skill metadata
        content = {
            "skill_id": result.skill_id,
            "path": str(result.path) if result.path else None,
            "message": result.message,
        }

        # Try to read the installed skill's metadata for richer output
        if result.path:
            try:
                parser = SkillParser()
                skill = parser.parse_file(result.path / "SKILL.md")
                content["name"] = skill.name
                content["description"] = skill.description
                content["version"] = skill.vibe_skill_version
                content["category"] = skill.category
                content["tags"] = skill.tags
                content["steps_count"] = len(skill.steps)
                content["variables"] = [
                    {"name": v.get("name"), "description": v.get("description")}
                    for v in skill.variables
                    if isinstance(v, dict)
                ]
            except Exception:
                # If parsing fails post-install, still report success
                pass

        return ToolResult(
            success=True,
            content=content,
        )


class SkillListTool(Tool):
    """List installed vibe skills."""

    def __init__(self, skills_dir: Path | str = "~/.vibe/skills"):
        super().__init__(
            name="skill_list",
            description="List all installed vibe skills with their metadata.",
        )
        self.installer = SkillInstaller(skills_dir=skills_dir)

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs) -> ToolResult:
        try:
            skills = self.installer.list_installed()
            content = {
                "count": len(skills),
                "skills": [
                    {
                        "id": skill_id,
                        "version": info.get("version", "?"),
                        "installed_at": info.get("installed_at", "?"),
                        "path": info.get("path", "?"),
                    }
                    for skill_id, info in skills.items()
                ],
            }
            return ToolResult(success=True, content=content)
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Failed to list skills: {e}",
            )
