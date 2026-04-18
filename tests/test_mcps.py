"""Tests for MCPBridge."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.tools.mcp_bridge import MCPBridge
from vibe.tools.tool_system import ToolResult


# ─── mcp_001: Discovery ───

@pytest.mark.asyncio
async def test_mcp_001_discovery_returns_tool_schemas():
    """mcp_001: get_tool_schemas returns list of available MCP tool schemas."""
    configs = [
        {
            "name": "filesystem",
            "description": "File system access",
            "url": "http://localhost:3000",
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
                {
                    "name": "list_dir",
                    "description": "List a directory",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            ],
        },
        {
            "name": "browser",
            "description": "Browser control",
            "url": "http://localhost:3001",
            "tools": [
                {
                    "name": "navigate",
                    "description": "Navigate to URL",
                    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
                },
            ],
        },
    ]
    bridge = MCPBridge(configs=configs)
    schemas = bridge.get_tool_schemas()

    assert len(schemas) == 3
    names = [s["function"]["name"] for s in schemas]
    assert "read_file" in names
    assert "list_dir" in names
    assert "navigate" in names

    # Verify schema structure
    for s in schemas:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert "parameters" in s["function"]


# ─── mcp_002: Execute successfully ───

@pytest.mark.asyncio
async def test_mcp_002_execute_runs_tool_successfully():
    """mcp_002: execute_tool invokes an HTTP MCP tool and returns success."""
    configs = [
        {
            "name": "filesystem",
            "url": "http://localhost:3000",
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            ],
        },
    ]
    bridge = MCPBridge(configs=configs)

    mock_response = MagicMock()
    mock_response.json.return_value = {"content": "hello world"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("vibe.tools.mcp_bridge.httpx.AsyncClient", return_value=mock_client):
        result = await bridge.execute_tool("read_file", path="/tmp/test.txt")

    assert result.success is True
    assert result.content == {"content": "hello world"}
    assert result.error is None

    # Verify the POST call
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://localhost:3000"
    assert call_args[1]["json"]["tool"] == "read_file"
    assert call_args[1]["json"]["arguments"] == {"path": "/tmp/test.txt"}


# ─── mcp_003: Invalid tool graceful error ───

@pytest.mark.asyncio
async def test_mcp_003_execute_invalid_tool_graceful_error():
    """mcp_003: execute_tool with unknown tool returns graceful error."""
    configs = [
        {
            "name": "filesystem",
            "url": "http://localhost:3000",
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            ],
        },
    ]
    bridge = MCPBridge(configs=configs)

    result = await bridge.execute_tool("nonexistent_tool", foo="bar")

    assert result.success is False
    assert result.content is None
    assert "nonexistent_tool" in result.error
    assert "not found" in result.error.lower()


# ─── Bonus: stdio execution ───

@pytest.mark.asyncio
async def test_mcp_stdio_execution():
    """Test stdio-based MCP tool execution."""
    configs = [
        {
            "name": "local-tool",
            "command": "echo",
            "args": [],
            "tools": [
                {
                    "name": "echo_tool",
                    "description": "Echo input",
                    "parameters": {"type": "object", "properties": {"msg": {"type": "string"}}},
                },
            ],
        },
    ]
    bridge = MCPBridge(configs=configs)

    # Mock asyncio subprocess
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}\n', b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await bridge.execute_tool("echo_tool", msg="hello")

    assert result.success is True
    assert result.content == {"result": "ok"}


@pytest.mark.asyncio
async def test_mcp_stdio_failure():
    """Test stdio-based MCP tool returns error on non-zero exit."""
    configs = [
        {
            "name": "local-tool",
            "command": "false",
            "args": [],
            "tools": [
                {
                    "name": "fail_tool",
                    "description": "Always fails",
                    "parameters": {"type": "object"},
                },
            ],
        },
    ]
    bridge = MCPBridge(configs=configs)

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error message"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await bridge.execute_tool("fail_tool")

    assert result.success is False
    assert "error message" in result.error
