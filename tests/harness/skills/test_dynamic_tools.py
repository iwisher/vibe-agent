"""Tests for dynamic tool declaration from skills."""

from vibe.harness.skills.dynamic_tools import (
    DynamicTool,
    DynamicToolRegistry,
    SkillToolDeclarator,
)


class TestDynamicToolRegistry:
    def test_register_tool(self):
        reg = DynamicToolRegistry()
        tool = DynamicTool(name="test_tool", description="A test tool")
        assert reg.register(tool) is True
        assert len(reg) == 1

    def test_register_duplicate(self):
        reg = DynamicToolRegistry()
        tool = DynamicTool(name="test_tool", description="A test tool")
        reg.register(tool)
        assert reg.register(tool) is False

    def test_unregister(self):
        reg = DynamicToolRegistry()
        tool = DynamicTool(name="test_tool", description="A test tool")
        reg.register(tool)
        assert reg.unregister("test_tool") is True
        assert len(reg) == 0
        assert reg.unregister("test_tool") is False

    def test_get_tool(self):
        reg = DynamicToolRegistry()
        tool = DynamicTool(name="test_tool", description="A test tool")
        reg.register(tool)
        assert reg.get_tool("test_tool") == tool
        assert reg.get_tool("nonexistent") is None

    def test_set_handler(self):
        reg = DynamicToolRegistry()
        tool = DynamicTool(name="test_tool", description="A test tool")
        reg.register(tool)

        def handler(x=1):
            return x * 2

        assert reg.set_handler("test_tool", handler) is True
        assert reg.get_handler("test_tool") == handler
        assert reg.get_handler("nonexistent") is None

    def test_get_schemas(self):
        reg = DynamicToolRegistry()
        tool = DynamicTool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        reg.register(tool)
        schemas = reg.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "test_tool"

    def test_contains(self):
        reg = DynamicToolRegistry()
        reg.register(DynamicTool(name="test", description="test"))
        assert "test" in reg
        assert "nonexistent" not in reg

    def test_clear(self):
        reg = DynamicToolRegistry()
        reg.register(DynamicTool(name="a", description="a"))
        reg.register(DynamicTool(name="b", description="b"))
        reg.clear()
        assert len(reg) == 0


class TestSkillToolDeclarator:
    def test_declare_from_skill(self):
        reg = DynamicToolRegistry()
        declarator = SkillToolDeclarator(reg)

        tools_meta = [
            {
                "name": "git_status",
                "description": "Show git status",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "git_log",
                "description": "Show git log",
                "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}},
            },
        ]

        registered = declarator.declare_from_skill("git-skill", tools_meta)
        assert len(registered) == 2
        assert "git_status" in registered
        assert reg.get_tool("git_status").skill_source == "git-skill"

    def test_create_handler_wrapper(self):
        reg = DynamicToolRegistry()
        declarator = SkillToolDeclarator(reg)

        class FakeExecutor:
            def handle_dynamic_tool(self, name, args):
                return {"tool": name, "args": args}

        handler = declarator.create_handler_wrapper("test_tool", FakeExecutor(), None)
        result = handler(x=1)
        assert result["tool"] == "test_tool"
        assert result["args"] == {"x": 1}

    def test_create_handler_wrapper_no_method(self):
        reg = DynamicToolRegistry()
        declarator = SkillToolDeclarator(reg)

        class FakeExecutor:
            pass

        handler = declarator.create_handler_wrapper("test_tool", FakeExecutor(), None)
        result = handler()
        assert "error" in result
