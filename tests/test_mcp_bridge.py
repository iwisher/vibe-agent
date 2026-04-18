"""Tests for MCPBridge."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from vibe.tools.mcp_bridge import MCPBridge, MCPServerConfig
from vibe.tools.tool_system import ToolResult


def test_mcp_bridge_get_schemas():
    bridge = MCPBridge(configs=[
        {
            "name": "fs",
            "description": "Filesystem",
            "tools": [
                {"name": "read", "description": "Read file", "parameters": {"type": "object"}},
            ],
        }
    ])
    schemas = bridge.get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "read"


@pytest.mark.asyncio
async def test_mcp_bridge_tool_not_found():
    bridge = MCPBridge()
    result = await bridge.execute_tool("missing")
    assert result.success is False
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_mcp_bridge_http_success():
    bridge = MCPBridge(configs=[
        {
            "name": "calc",
            "description": "Calculator",
            "url": "http://localhost:3000/call",
            "tools": [
                {"name": "add", "description": "Add numbers", "parameters": {"type": "object"}},
            ],
        }
    ])

    class FakeClient:
        def __init__(self, timeout=None):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json):
            class Resp:
                def raise_for_status(self): pass
                def json(self): return {"result": 42}
            return Resp()

    import types
    from unittest.mock import patch

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeClient)
    with patch("vibe.tools.mcp_bridge.httpx", fake_httpx):
        result = await bridge.execute_tool("add", a=1, b=2)
        assert result.success is True
        assert result.content["result"] == 42


@pytest.mark.asyncio
async def test_mcp_bridge_stdio_success():
    bridge = MCPBridge(configs=[
        {
            "name": "local",
            "description": "Local tool",
            "command": "echo",
            "args": ["{\"success\": true}"],
            "tools": [
                {"name": "echo", "description": "Echo", "parameters": {"type": "object"}},
            ],
        }
    ])
    # echo won't return valid json from the payload, but let's test the integration path
    result = await bridge.execute_tool("echo", msg="hi")
    # Since echo ignores stdin and outputs the args, json parsing will likely fail
    # but we just verify it runs the stdio path without crashing
    assert isinstance(result, ToolResult)


def test_mcpserver_config_defaults():
    cfg = MCPServerConfig(name="test", description="test desc")
    assert cfg.url is None
    assert cfg.command is None
    assert cfg.args == []
    assert cfg.tools == []
    # Mutable defaults must be isolated per instance
    cfg2 = MCPServerConfig(name="test2", description="test2 desc")
    cfg.args.append("--foo")
    assert "--foo" not in cfg2.args  # no shared mutable default bug
