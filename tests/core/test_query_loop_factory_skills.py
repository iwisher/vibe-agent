"""Tests for QueryLoopFactory skill tool wiring."""

from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.tools.skill_install import SkillInstallTool, SkillListTool
from vibe.tools.skill_manage import SkillManageTool


def test_skill_tools_are_registered():
    """Assert that all skill tools are registered in create_tool_system."""
    factory = QueryLoopFactory(
        base_url="http://localhost:11434",
        model="llama3.2",
    )
    tool_system = factory.create_tool_system()

    assert "skill_install" in tool_system.list_tools()
    assert "skill_list" in tool_system.list_tools()
    assert "skill_manage" in tool_system.list_tools()

    assert isinstance(tool_system._tools["skill_install"], SkillInstallTool)
    assert isinstance(tool_system._tools["skill_list"], SkillListTool)
    assert isinstance(tool_system._tools["skill_manage"], SkillManageTool)
