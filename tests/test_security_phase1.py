"""Phase 1 security tests for file tools and skill management.

Covers:
- Symlink escape blocking in ReadFileTool / WriteFileTool
- Path traversal blocking in SkillManageTool
- MCPServerConfig mutable default isolation
"""

from pathlib import Path

import pytest

from vibe.tools.file import ReadFileTool, WriteFileTool
from vibe.tools.mcp_bridge import MCPServerConfig
from vibe.tools.skill_manage import SkillManageTool

# ---------------------------------------------------------------------------
# File tool symlink escape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_001_symlink_escape_blocked(tmp_path):
    """A symlink inside root_dir pointing outside must be blocked."""
    # Create jail and a symlink inside it pointing outside
    jail = tmp_path / "jail"
    jail.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("secret")
    symlink = jail / "link_to_secret"
    symlink.symlink_to(target)

    tool = ReadFileTool(root_dir=str(jail))
    result = await tool.execute(path=str(symlink))
    assert not result.success
    assert "escapes" in result.error.lower()


@pytest.mark.asyncio
async def test_file_002_dotdot_escape_blocked(tmp_path):
    """Path traversal via .. components must be blocked."""
    jail = tmp_path / "jail"
    jail.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")

    tool = ReadFileTool(root_dir=str(jail))
    result = await tool.execute(path="../secret.txt")
    assert not result.success
    assert "escapes" in result.error.lower()


@pytest.mark.asyncio
async def test_file_003_symlink_inside_jail_allowed(tmp_path):
    """A symlink inside root_dir pointing to another file inside jail is OK."""
    jail = tmp_path / "jail"
    jail.mkdir()
    real_file = jail / "real.txt"
    real_file.write_text("hello")
    symlink = jail / "link_to_real"
    symlink.symlink_to(real_file)

    tool = ReadFileTool(root_dir=str(jail))
    result = await tool.execute(path=str(symlink))
    assert result.success
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_file_004_write_symlink_escape_blocked(tmp_path):
    """WriteFileTool must also block symlink escapes."""
    jail = tmp_path / "jail"
    jail.mkdir()
    target = tmp_path / "passwd"
    target.write_text("original")
    symlink = jail / "link_to_passwd"
    symlink.symlink_to(target)

    tool = WriteFileTool(root_dir=str(jail))
    result = await tool.execute(path=str(symlink), content="hacked")
    assert not result.success
    assert "escapes" in result.error.lower()
    # Ensure target was NOT overwritten
    assert target.read_text() == "original"


# ---------------------------------------------------------------------------
# SkillManageTool path traversal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skill_001_path_traversal_blocked(tmp_path):
    """Skill names containing .. must be blocked."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="../evil", content="x")
    assert not result.success
    assert "traversal" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_002_category_traversal_blocked(tmp_path):
    """Category names containing .. must be blocked."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="good", category="../evil", content="x")
    assert not result.success
    assert "traversal" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_003_absolute_name_blocked(tmp_path):
    """An absolute path as skill name must be blocked."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    result = await tool.execute(action="create", name="/etc/passwd", content="x")
    assert not result.success
    assert "traversal" in result.error.lower()


@pytest.mark.asyncio
async def test_skill_004_valid_skill_allowed(tmp_path):
    """Normal skill creation inside the jail should succeed."""
    tool = SkillManageTool(skills_dir=str(tmp_path / "skills"))
    content = """+++
vibe_skill_version = "2.0.0"
id = "my-skill"
name = "My Skill"
description = "A test skill"
category = "test"
tags = ["test"]

[trigger]
patterns = ["test"]
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "Hello"
tool = "bash"
command = "echo hello"
+++

# My Skill
"""
    result = await tool.execute(action="create", name="my-skill", content=content)
    assert result.success
    assert (tmp_path / "skills" / "my-skill" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# MCPServerConfig mutable defaults
# ---------------------------------------------------------------------------

def test_mcp_001_mutable_defaults_isolated():
    """Mutating one config's args must not affect another config."""
    cfg1 = MCPServerConfig(name="a", description="A")
    cfg2 = MCPServerConfig(name="b", description="B")
    cfg1.args.append("--foo")
    assert "--foo" not in cfg2.args
    cfg1.tools.append({"name": "t1"})
    assert len(cfg2.tools) == 0


# ─── 5-Layer SecurityCoordinator tests ───

from vibe.core.config import SecurityConfig
from vibe.core.coordinators import SecurityCoordinator


def test_security_layer1_pattern_blocks_critical():
    """Layer 1: Critical patterns like rm -rf / must be blocked."""
    config = SecurityConfig(approval_mode="auto")
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("bash", {"command": "rm -rf /"})
    assert not result.allowed
    assert result.layer == "pattern_scan"
    assert "Critical pattern" in result.reason


def test_security_layer1_pattern_allows_safe():
    """Layer 1: Safe commands should pass pattern scanning."""
    config = SecurityConfig(approval_mode="auto")
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("bash", {"command": "echo hello"})
    assert result.allowed


def test_security_layer2_file_safety_blocks_denylist():
    """Layer 2: Writing to denylisted paths must be blocked."""
    config = SecurityConfig(
        approval_mode="auto",
        file_safety={"write_denylist_enabled": True, "read_blocklist_enabled": True, "safe_root": str(Path.home())},
    )
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("write_file", {"path": "~/.ssh/authorized_keys", "content": "x"})
    assert not result.allowed
    assert result.layer == "file_safety"


def test_security_layer2_file_safety_allows_safe_path():
    """Layer 2: Safe paths should pass file safety."""
    config = SecurityConfig(
        approval_mode="auto",
        file_safety={"write_denylist_enabled": True, "read_blocklist_enabled": True, "safe_root": str(Path.home())},
    )
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("write_file", {"path": "~/workspace/test.txt", "content": "x"})
    assert result.allowed


def test_security_layer3_human_approval_strict_blocks():
    """Layer 3: STRICT approval mode should block destructive tools."""
    config = SecurityConfig(approval_mode="strict")
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("bash", {"command": "echo hello"})
    assert not result.allowed
    assert result.layer == "human_approval"


def test_security_layer3_human_approval_auto_allows():
    """Layer 3: AUTO approval mode should allow destructive tools."""
    config = SecurityConfig(approval_mode="auto")
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("bash", {"command": "echo hello"})
    assert result.allowed


def test_security_layer4_smart_approver_blocks_high_risk():
    """Layer 4: Smart approver heuristic should block critical-risk content."""
    config = SecurityConfig(approval_mode="auto", smart_approver_enabled=True)
    # No LLM client -> uses heuristics only
    coord = SecurityCoordinator(config=config, llm_client=None)

    # write_file with eval( passes pattern scanning (eval is WARNING, not CRITICAL)
    # but smart approver catches eval( inside the content argument
    result = coord.evaluate_tool_call("write_file", {"path": "/tmp/x", "content": "eval(1+1)"})
    # Heuristic should flag eval( in content as critical risk and reject
    assert not result.allowed
    assert result.layer == "smart_approver"
    assert result.risk_level in ("high", "critical")


def test_security_layer5_checkpoint_creates_rollback():
    """Layer 5: Checkpoints should be created for destructive tools."""
    from vibe.tools.security.checkpoints import CheckpointManager

    cp = CheckpointManager()
    config = SecurityConfig(approval_mode="auto", checkpoint_enabled=True)
    coord = SecurityCoordinator(config=config, checkpoint_manager=cp)

    result = coord.evaluate_tool_call("bash", {"command": "echo hello"})
    assert result.allowed
    assert result.checkpoint_id is not None


def test_security_layer5_no_checkpoint_for_safe_tools():
    """Layer 5: Safe tools should not trigger checkpoint creation."""
    from vibe.tools.security.checkpoints import CheckpointManager

    cp = CheckpointManager()
    config = SecurityConfig(approval_mode="auto", checkpoint_enabled=True)
    coord = SecurityCoordinator(config=config, checkpoint_manager=cp)

    result = coord.evaluate_tool_call("read_file", {"path": "hello.txt"})
    assert result.allowed
    assert result.checkpoint_id is None


def test_security_disabled_passes_all():
    """When all layers are disabled, everything passes."""
    config = SecurityConfig(
        approval_mode="auto",
        dangerous_patterns_enabled=False,
        smart_approver_enabled=False,
        checkpoint_enabled=False,
    )
    coord = SecurityCoordinator(config=config)

    result = coord.evaluate_tool_call("bash", {"command": "echo hello"})
    assert result.allowed


# ---------------------------------------------------------------------------
# QueryLoop integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_loop_security_blocks_destructive_tool():
    """QueryLoop with security should block destructive tool calls."""
    from unittest.mock import AsyncMock, MagicMock

    from vibe.core.query_loop import QueryLoop
    from vibe.tools.tool_system import ToolSystem

    tool_system = ToolSystem()
    mock_llm = MagicMock()
    mock_llm.model = "test"
    mock_llm.complete = AsyncMock(return_value=MagicMock(
        content="",
        tool_calls=[{"id": "c1", "function": {"name": "bash", "arguments": '{"command": "rm -rf /"}'}}],
        is_error=False,
        usage={},
    ))

    security_config = SecurityConfig(approval_mode="strict")
    loop = QueryLoop(
        llm_client=mock_llm,
        tool_system=tool_system,
        security_config=security_config,
    )

    results = [r async for r in loop.run("do something")]
    # The loop should process the tool call and security should block it
    assert len(results) >= 1
    # Check that a tool result with error was generated
    assert any("Security blocked" in str(r.tool_results) for r in results if r.tool_results)
