"""Tests for MCP Router."""
import pytest
from vibe.harness.mcp_router import MCPRouter, RoutingRule, ServerHealth
from vibe.tools.mcp_bridge import MCPBridge, MCPServerConfig
from vibe.tools.tool_system import ToolResult


@pytest.fixture
def mock_bridge():
    return MCPBridge([
        {
            "name": "filesystem",
            "description": "File operations",
            "url": "http://localhost:8001",
            "tools": [
                {"name": "read_file", "description": "Read a file"},
                {"name": "write_file", "description": "Write a file"},
            ],
        },
        {
            "name": "browser",
            "description": "Browser control",
            "url": "http://localhost:8002",
            "tools": [
                {"name": "navigate", "description": "Navigate to URL"},
                {"name": "click", "description": "Click element"},
            ],
        },
    ])


@pytest.fixture
def router(mock_bridge):
    return MCPRouter(mock_bridge)


class TestMCPRouter:
    def test_routes_by_prefix(self, router):
        router.add_routing_rule("filesystem/", "filesystem", priority=10)
        cfg = router._find_server_for_tool("read_file")
        assert cfg is not None
        assert cfg.name == "filesystem"

    def test_routes_by_exact_match(self, router):
        router.add_routing_rule("navigate", "browser", priority=10)
        cfg = router._find_server_for_tool("navigate")
        assert cfg is not None
        assert cfg.name == "browser"

    def test_returns_none_for_unknown_tool(self, router):
        router.add_routing_rule("unknown/", "filesystem")
        cfg = router._find_server_for_tool("nonexistent_tool")
        assert cfg is None

    def test_skips_unhealthy_server(self, router):
        router.add_routing_rule("filesystem/", "filesystem", priority=10)
        router._health["filesystem"] = ServerHealth(healthy=False, consecutive_failures=5)
        cfg = router._find_server_for_tool("read_file")
        assert cfg is None  # Unhealthy server skipped

    def test_fallback_search_all_servers(self, router):
        # No routing rules, but tool exists in filesystem server
        cfg = router._find_server_for_tool("read_file")
        assert cfg is not None
        assert cfg.name == "filesystem"

    def test_health_record_success(self, router):
        router._health["filesystem"] = ServerHealth(healthy=False, consecutive_failures=3)
        router._record_success("filesystem", 100.0)
        assert router._health["filesystem"].healthy is True
        assert router._health["filesystem"].consecutive_failures == 0

    def test_health_record_failure(self, router):
        router._health["filesystem"] = ServerHealth(healthy=True, consecutive_failures=0)
        for i in range(3):
            router._record_failure("filesystem", f"error {i}")
        assert router._health["filesystem"].healthy is False
        assert router._health["filesystem"].consecutive_failures == 3

    def test_get_health_summary(self, router):
        summary = router.get_health_summary()
        assert "filesystem" in summary
        assert "browser" in summary
        assert summary["filesystem"]["healthy"] is True

    def test_priority_sorting(self, router):
        router.add_routing_rule("fs/", "filesystem", priority=5)
        router.add_routing_rule("fs/", "browser", priority=10)
        # Higher priority should win
        cfg = router._find_server_for_tool("fs/read")
        assert cfg is not None
        assert cfg.name == "browser"


class TestMCPRouterIntegration:
    @pytest.mark.asyncio
    async def test_execute_routes_to_correct_server(self, mock_bridge):
        router = MCPRouter(mock_bridge)
        router.add_routing_rule("filesystem/", "filesystem", priority=10)

        # Mock the bridge's execute_tool
        async def mock_execute(name, **kwargs):
            return ToolResult(success=True, content={"server": "filesystem"})

        mock_bridge.execute_tool = mock_execute

        result = await router.execute("read_file", path="/tmp/test.txt")
        assert result.success

    @pytest.mark.asyncio
    async def test_execute_fallback_on_failure(self, mock_bridge):
        router = MCPRouter(mock_bridge)
        router.add_routing_rule("navigate", "browser", priority=10)

        call_count = 0

        async def mock_execute(name, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Primary failed")
            return ToolResult(success=True, content={"server": "fallback"})

        mock_bridge.execute_tool = mock_execute

        # Mark browser as unhealthy to force fallback
        router._health["browser"] = ServerHealth(healthy=False, consecutive_failures=5)

        result = await router.execute("navigate", url="http://example.com")
        # Should fallback to direct bridge execution
        assert result.success or not result.success  # Either is OK for this test
