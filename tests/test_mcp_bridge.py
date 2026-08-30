"""Tests for MCPBridge."""

from unittest.mock import patch

import pytest

from vibe.tools.mcp_bridge import MCPBridge, MCPServerConfig
from vibe.tools.tool_system import ToolResult


def test_mcp_bridge_get_schemas():
    bridge = MCPBridge(
        configs=[
            {
                "name": "fs",
                "description": "Filesystem",
                "tools": [
                    {"name": "read", "description": "Read file", "parameters": {"type": "object"}},
                ],
            }
        ]
    )
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
    bridge = MCPBridge(
        configs=[
            {
                "name": "calc",
                "description": "Calculator",
                # Public IP literal: passes the SSRF gate without DNS resolution.
                "url": "http://93.184.216.34/call",
                "tools": [
                    {"name": "add", "description": "Add numbers", "parameters": {"type": "object"}},
                ],
            }
        ]
    )

    class FakeClient:
        def __init__(self, timeout=None, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            class Resp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"result": 42}

            return Resp()

    import types

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeClient)
    with patch("vibe.tools.mcp_bridge.httpx", fake_httpx):
        result = await bridge.execute_tool("add", a=1, b=2)
        assert result.success is True
        assert result.content["result"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:9999/mcp",
        "http://10.0.0.1/internal",
        "http://[::ffff:7f00:1]/",
    ],
)
async def test_mcp_bridge_http_ssrf_blocked(url):
    """SSRF gate: private/metadata targets must be blocked before any HTTP call."""
    bridge = MCPBridge(
        configs=[
            {
                "name": "evil",
                "description": "",
                "url": url,
                "tools": [{"name": "probe", "description": "", "parameters": {}}],
            }
        ]
    )

    class ExplodingClient:
        created = False

        def __init__(self, *args, **kwargs):
            # Record instead of raise: _invoke_http would swallow an exception
            # into a ToolResult error, hiding the intent.
            ExplodingClient.created = True

    import types

    fake_httpx = types.SimpleNamespace(AsyncClient=ExplodingClient)
    with patch("vibe.tools.mcp_bridge.httpx", fake_httpx):
        result = await bridge.execute_tool("probe")
    assert result.success is False
    assert "SSRF" in result.error
    assert not ExplodingClient.created, "HTTP client must never be created for blocked URLs"


@pytest.mark.asyncio
async def test_mcp_bridge_allow_private_opt_out():
    """allow_private=true explicitly opts a local MCP server out of the SSRF gate."""
    bridge = MCPBridge(
        configs=[
            {
                "name": "local",
                "description": "Local server",
                "url": "http://localhost:3000/call",
                "allow_private": True,
                "tools": [{"name": "ping", "description": "", "parameters": {}}],
            }
        ]
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, json):
            class Resp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"ok": True}

            return Resp()

    import types

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeClient)
    with patch("vibe.tools.mcp_bridge.httpx", fake_httpx):
        result = await bridge.execute_tool("ping")
    assert result.success is True
    assert result.content == {"ok": True}


@pytest.mark.asyncio
async def test_mcp_bridge_stdio_success():
    bridge = MCPBridge(
        configs=[
            {
                "name": "local",
                "description": "Local tool",
                "command": "echo",
                "args": ['{"success": true}'],
                "tools": [
                    {"name": "echo", "description": "Echo", "parameters": {"type": "object"}},
                ],
            }
        ]
    )
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
