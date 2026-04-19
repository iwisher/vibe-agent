"""MCP bridge for exposing external tool servers to the Vibe Agent harness."""

import json
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

from vibe.tools.tool_system import ToolResult


@dataclass
class MCPServerConfig:
    name: str
    description: str
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)


class MCPBridge:
    """Lightweight bridge that exposes MCP server tools as callable schemas.

    For HTTP-based MCP servers, performs POST requests with connection pooling.
    For command-based (stdio) servers, spawns subprocesses.
    """

    def __init__(self, configs: list[dict[str, Any] | None] = None):
        self.configs: list[MCPServerConfig] = []
        self._http_clients: dict[str, Any] = {}  # url -> httpx.AsyncClient
        for cfg in configs or []:
            self.configs.append(
                MCPServerConfig(
                    name=cfg.get("name", "mcp"),
                    description=cfg.get("description", ""),
                    url=cfg.get("url"),
                    command=cfg.get("command"),
                    args=cfg.get("args", []),
                    tools=cfg.get("tools", []),
                )
            )

    def _get_http_client(self, url: str) -> Any:
        """Get or create a cached HTTP client for the given URL."""
        if url in self._http_clients:
            return self._http_clients[url]
        if httpx is None:
            raise RuntimeError("httpx is not installed")
        client = httpx.AsyncClient(timeout=30.0)
        self._http_clients[url] = client
        return client

    async def close(self) -> None:
        """Close all cached HTTP clients."""
        for client in self._http_clients.values():
            await client.aclose()
        self._http_clients.clear()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for cfg in self.configs:
            for tool in cfg.tools:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", "unknown"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object"}),
                    },
                })
        return schemas

    async def execute_tool(self, name: str, **kwargs) -> ToolResult:
        for cfg in self.configs:
            for tool in cfg.tools:
                if tool.get("name") == name:
                    return await self._invoke(cfg, tool, kwargs)
        return ToolResult(success=False, content=None, error=f"MCP tool '{name}' not found")

    async def _invoke(self, cfg: MCPServerConfig, tool: dict[str, Any], arguments: dict[str, Any]) -> ToolResult:
        if cfg.url:
            return await self._invoke_http(cfg.url, tool, arguments)
        if cfg.command:
            return await self._invoke_stdio(cfg, tool, arguments)
        return ToolResult(success=False, content=None, error=f"MCP server '{cfg.name}' has no transport configured")

    async def _invoke_http(self, url: str, tool: dict[str, Any], arguments: dict[str, Any]) -> ToolResult:
        if httpx is None:
            return ToolResult(success=False, content=None, error="httpx is not installed")
        try:
            payload = {
                "tool": tool.get("name"),
                "arguments": arguments,
            }
            client = self._get_http_client(url)
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return ToolResult(success=True, content=data)
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))

    async def _invoke_stdio(self, cfg: MCPServerConfig, tool: dict[str, Any], arguments: dict[str, Any]) -> ToolResult:
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                cfg.command,
                *cfg.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            payload = json.dumps({"tool": tool.get("name"), "arguments": arguments}) + "\n"
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload.encode()), timeout=30.0)
            if proc.returncode != 0:
                return ToolResult(success=False, content=None, error=stderr.decode().strip() or "Subprocess failed")
            return ToolResult(success=True, content=json.loads(stdout.decode().strip()))
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
