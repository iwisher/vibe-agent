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

    def _prompt_with_timeout(self, prompt: str, timeout: float) -> str | None:
        """Read a line from stdin with a timeout. Returns None on timeout.

        Uses select.select() on POSIX systems and falls back to a threading-based
        approach on Windows where select() does not support stdin.
        """
        import sys
        import threading

        print(prompt, end="", flush=True)

        # Primary path: POSIX select()
        try:
            import select

            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                return sys.stdin.readline().strip()
            return None
        except (ImportError, OSError, ValueError):
            # Fallback for Windows where select.select() doesn't support stdin
            response: list[str | None] = [None]

            def _read() -> None:
                try:
                    response[0] = sys.stdin.readline()
                except Exception:
                    pass

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout)
            return response[0].strip() if response[0] is not None else None

    def approve(
        self,
        skill_name: str,
        risks: list[str],
        warnings: list[str],
    ) -> bool:
        # Block on critical risks unless config gates allow interactive CLI prompts
        if risks:
            import sys

            try:
                from vibe.core.config import VibeConfig

                config = VibeConfig.load()
                interactive_enabled = config.security.interactive_skill_install
            except Exception:
                interactive_enabled = False

            if interactive_enabled and sys.stdin.isatty():
                print("\n⚠️  SECURITY RISK WARNING")
                print(f"Skill '{skill_name}' contains critical security risks:")
                for risk in risks:
                    print(f" - {risk}")
                response = self._prompt_with_timeout(
                    "\nDo you want to override and install this skill anyway? "
                    "[y/N] (30s timeout): ",
                    30.0,
                )
                if response == "y":
                    return True
                if response is None:
                    print("\n[Timeout - Auto-rejecting skill installation]")
            return False

        # Auto-approve warnings in chat context
        return True


class SkillInstallExecutableTool(Tool):
    """Install an executable vibe skill from git, local path, or tarball URL.

    Executable skills contain [[steps]] that run via the ToolSystem when
    triggered by the `run_skill` tool. They are validated for security
    (dangerous commands, pipe-to-shell attacks, etc.) before installation.
    """

    def __init__(
        self,
        skills_dir: Path | str = "~/.vibe/skills",
        approval_gate: ApprovalGate | None = None,
    ):
        super().__init__(
            name="skill_install_executable",
            description=(
                "Install an executable vibe skill from a git repository URL, "
                "local directory path, or tarball URL. Executable skills contain "
                "steps that run via the ToolSystem. They are validated for security "
                "before installation. Returns the installed skill's metadata on success."
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

        lines = [result.message]
        if result.path:
            lines.append(f"Path: {result.path}")

        # Try to read the installed skill's metadata for richer output
        if result.path:
            try:
                parser = SkillParser()
                skill = parser.parse_file(result.path / "SKILL.md")
                lines.append(f"Name: {skill.name}")
                lines.append(f"Description: {skill.description}")
                lines.append(f"Version: {skill.vibe_skill_version}")
                lines.append(f"Category: {skill.category}")
                if skill.tags:
                    lines.append(f"Tags: {', '.join(skill.tags)}")
                lines.append(f"Steps: {len(skill.steps)}")
            except Exception:
                # If parsing fails post-install, still report success
                pass

        return ToolResult(
            success=True,
            content="\n".join(lines),
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
            if not skills:
                return ToolResult(success=True, content="No skills installed.")

            lines = [f"Installed skills: {len(skills)}"]
            for skill_id, info in skills.items():
                version = info.get("version", "?")
                installed = info.get("installed_at", "?")[:10]
                path = info.get("path", "?")
                lines.append(f"- {skill_id} (v{version}, installed {installed})")
                lines.append(f"  Path: {path}")

            return ToolResult(success=True, content="\n".join(lines))
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Failed to list skills: {e}",
            )
