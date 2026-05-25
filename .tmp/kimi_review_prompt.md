You are a code reviewer. Review the following diff for a Python project (vibe-agent).

Focus on:
1. Code correctness and edge cases
2. Security considerations
3. Test coverage gaps
4. Style/consistency with the codebase
5. Any bugs or issues

Be concise but thorough. Point out specific line numbers or function names.

## Diff

```diff
diff --git a/vibe/core/query_loop_factory.py b/vibe/core/query_loop_factory.py
index 04efdf3..a454add 100644
--- a/vibe/core/query_loop_factory.py
+++ b/vibe/core/query_loop_factory.py
@@ -12,6 +12,9 @@ from vibe.tools.file import ReadFileTool, WriteFileTool
 from vibe.tools.tool_system import ToolSystem
 
 
+from vibe.tools.skill_install import SkillInstallTool, SkillListTool
+
+
 class QueryLoopFactory:
     """Centralized factory for creating QueryLoop instances with consistent wiring."""
 
@@ -112,6 +115,8 @@ class QueryLoopFactory:
         )
         tool_system.register_tool(ReadFileTool())
         tool_system.register_tool(WriteFileTool())
+        tool_system.register_tool(SkillInstallTool())
+        tool_system.register_tool(SkillListTool())
         return tool_system
```

## New File: vibe/tools/skill_install.py

```python
"""Tool for installing vibe skills interactively during chat sessions."""

from pathlib import Path
from typing import Any

from vibe.harness.skills.approval import ApprovalGate, AutoApproveGate, AutoRejectGate
from vibe.harness.skills.installer import InstallResult, SkillInstaller
from vibe.harness.skills.parser import SkillParser
from vibe.harness.skills.validator import SkillValidator

from .tool_system import Tool, ToolResult


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
        elif source.startswith("http") or source.endswith(".git"):
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
```

## New File: tests/tools/test_skill_install.py

```python
"""Unit tests for skill_install and skill_list tools and ChatApprovalGate."""

import tempfile
from pathlib import Path
import pytest

from vibe.tools.skill_install import SkillInstallTool, SkillListTool, ChatApprovalGate
from vibe.tools.tool_system import ToolResult

SAMPLE_SKILL = """+++
vibe_skill_version = "2.0.0"
id = "sample-skill"
name = "Sample Skill"
description = "A sample skill"
category = "test"
tags = ["test"]

[trigger]
patterns = ["sample"]
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "Hello"
tool = "bash"
command = "echo hello"
+++

# Sample Skill
"""


@pytest.mark.asyncio
async def test_skill_install_tool_schema():
    tool = SkillInstallTool()
    schema = tool.get_schema()
    assert schema["type"] == "object"
    assert "source" in schema["properties"]
    assert "skill_id" in schema["properties"]
    assert "source" in schema["required"]


@pytest.mark.asyncio
async def test_skill_install_tool_missing_source():
    tool = SkillInstallTool()
    result = await tool.execute()
    assert not result.success
    assert "Missing required parameter: 'source'" in result.error


@pytest.mark.asyncio
async def test_skill_install_tool_local_path_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # Create a source skill directory
        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        tool = SkillInstallTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir))

        assert result.success
        assert result.content["skill_id"] == "sample-skill"
        assert result.content["name"] == "Sample Skill"
        assert result.content["description"] == "A sample skill"
        assert result.content["version"] == "2.0.0"
        assert result.content["category"] == "test"
        assert result.content["tags"] == ["test"]
        assert result.content["steps_count"] == 1
        assert (skills_dir / "sample-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_skill_install_tool_local_path_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        tool = SkillInstallTool(skills_dir=skills_dir)
        result = await tool.execute(source="/nonexistent/path/to/skill")
        assert not result.success
        assert "Path not found" in result.error


@pytest.mark.asyncio
async def test_skill_list_tool_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        tool = SkillListTool(skills_dir=skills_dir)
        result = await tool.execute()
        assert result.success
        assert result.content["count"] == 0
        assert result.content["skills"] == []


@pytest.mark.asyncio
async def test_skill_list_tool_with_skills():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = (tmp_path / "skills").resolve()
        skills_dir.mkdir()

        # Pre-install a skill by using SkillInstallTool
        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        install_tool = SkillInstallTool(skills_dir=skills_dir)
        install_res = await install_tool.execute(source=str(source_dir))
        assert install_res.success

        list_tool = SkillListTool(skills_dir=skills_dir)
        result = await list_tool.execute()
        assert result.success
        assert result.content["count"] == 1
        skill_info = result.content["skills"][0]
        assert skill_info["id"] == "sample-skill"
        assert skill_info["version"] == "2.0.0"
        assert "installed_at" in skill_info
        assert skill_info["path"] == str(skills_dir / "sample-skill")


def test_chat_approval_gate_blocks_risks():
    gate = ChatApprovalGate()
    # risks present
    assert gate.approve("Sample", risks=["critical vulnerability"], warnings=[]) is False
    assert gate.approve("Sample", risks=["critical vulnerability"], warnings=["some warning"]) is False


def test_chat_approval_gate_approves_warnings():
    gate = ChatApprovalGate()
    # warnings present, no risks
    assert gate.approve("Sample", risks=[], warnings=["warning one", "warning two"]) is True
    # nothing present
    assert gate.approve("Sample", risks=[], warnings=[]) is True


@pytest.mark.asyncio
async def test_format_result_includes_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        source_dir = tmp_path / "my-skill"
        source_dir.mkdir()
        (source_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")

        tool = SkillInstallTool(skills_dir=skills_dir)
        result = await tool.execute(source=str(source_dir))

        assert result.success
        assert "name" in result.content
        assert "description" in result.content
        assert "version" in result.content
        assert "category" in result.content
        assert "tags" in result.content
        assert "steps_count" in result.content
        assert "variables" in result.content
```

## New File: tests/core/test_query_loop_factory_skills.py

```python
"""Integration tests for QueryLoopFactory skill tools wiring."""

from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.tools.skill_install import SkillInstallTool, SkillListTool


def test_create_tool_system_includes_skill_tools():
    """Verify that QueryLoopFactory registers the skill tools in its system."""
    factory = QueryLoopFactory(
        base_url="http://localhost:11434",
        model="llama3.2",
    )
    tool_system = factory.create_tool_system()

    assert "skill_install" in tool_system.list_tools()
    assert "skill_list" in tool_system.list_tools()

    install_tool = tool_system._tools["skill_install"]
    list_tool = tool_system._tools["skill_list"]

    assert isinstance(install_tool, SkillInstallTool)
    assert isinstance(list_tool, SkillListTool)
```

Provide your review as a numbered list of issues/findings. If everything looks good, say "LGTM".
