"""MCP bridge for exposing external tool servers to the Vibe Agent harness."""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

from vibe.tools.tool_system import ToolResult


@dataclass
class MCPServerConfig:
    name: str
    description: str
    url: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)


class MCPBridge:
    """Lightweight bridge that exposes MCP server tools as callable schemas.

    For HTTP-based MCP servers, performs POST requests.
    For command-based (stdio) servers, spawns subprocesses.
    """

    def __init__(self, configs: Optional[List[Dict[str, Any]]] = None):
        self.configs: List[MCPServerConfig] = []
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

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
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

    async def _invoke(self, cfg: MCPServerConfig, tool: Dict[str, Any], arguments: Dict[str, Any]) -> ToolResult:
        if cfg.url:
            return await self._invoke_http(cfg.url, tool, arguments)
        if cfg.command:
            return await self._invoke_stdio(cfg, tool, arguments)
        return ToolResult(success=False, content=None, error=f"MCP server '{cfg.name}' has no transport configured")

    async def _invoke_http(self, url: str, tool: Dict[str, Any], arguments: Dict[str, Any]) -> ToolResult:
        if httpx is None:
            return ToolResult(success=False, content=None, error="httpx is not installed")
        try:
            payload = {
                "tool": tool.get("name"),
                "arguments": arguments,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return ToolResult(success=True, content=data)
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))

    async def _invoke_stdio(self, cfg: MCPServerConfig, tool: Dict[str, Any], arguments: Dict[str, Any]) -> ToolResult:
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
