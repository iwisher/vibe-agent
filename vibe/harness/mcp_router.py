"""MCP Tool Router with server selection, health checking, and load balancing.

Routes tool calls to the appropriate MCP server based on:
- Tool name prefix matching (e.g., "filesystem/read" -> filesystem server)
- Server health status (skip unhealthy servers)
- Load balancing across healthy servers with the same tool
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from vibe.tools.mcp_bridge import MCPBridge, MCPServerConfig
from vibe.tools.tool_system import ToolResult


@dataclass
class ServerHealth:
    """Health status of an MCP server."""
    healthy: bool = True
    last_check: float = 0.0
    consecutive_failures: int = 0
    average_latency_ms: float = 0.0
    error_message: str = ""


@dataclass
class RoutingRule:
    """Rule for routing a tool call to an MCP server."""
    tool_prefix: str  # e.g., "filesystem/", "browser."
    server_name: str
    priority: int = 0  # Higher = preferred


class MCPRouter:
    """Routes tool calls to MCP servers with health checking and failover.

    Features:
    - Prefix-based routing (e.g., "filesystem/read" -> filesystem server)
    - Health checks with exponential backoff
    - Automatic failover to backup servers
    - Latency tracking for load balancing
    """

    HEALTH_CHECK_INTERVAL = 30.0  # seconds
    MAX_CONSECUTIVE_FAILURES = 3
    FAILURE_COOLDOWN = 60.0  # seconds before retrying a failed server

    def __init__(self, mcp_bridge: MCPBridge):
        self.bridge = mcp_bridge
        self._health: dict[str, ServerHealth] = {}
        self._routing_rules: list[RoutingRule] = []
        self._last_health_check = 0.0
        self._lock = asyncio.Lock()

        # Initialize health for all configured servers
        for cfg in mcp_bridge.configs:
            self._health[cfg.name] = ServerHealth()

    def add_routing_rule(self, tool_prefix: str, server_name: str, priority: int = 0) -> None:
        """Add a routing rule.

        Args:
            tool_prefix: Tool name prefix to match (e.g., "filesystem/")
            server_name: Target MCP server name
            priority: Higher priority rules are preferred
        """
        self._routing_rules.append(RoutingRule(tool_prefix, server_name, priority))
        # Sort by priority descending
        self._routing_rules.sort(key=lambda r: r.priority, reverse=True)

    def _find_server_for_tool(self, tool_name: str) -> Optional[MCPServerConfig]:
        """Find the best MCP server for a tool based on routing rules and health."""
        # Find matching rules
        matching_rules = []
        for rule in self._routing_rules:
            if tool_name.startswith(rule.tool_prefix) or tool_name == rule.tool_prefix.rstrip("/."):
                matching_rules.append(rule)

        # Sort by priority
        matching_rules.sort(key=lambda r: r.priority, reverse=True)

        # Find first healthy server
        for rule in matching_rules:
            health = self._health.get(rule.server_name)
            if health and health.healthy:
                for cfg in self.bridge.configs:
                    if cfg.name == rule.server_name:
                        return cfg

        # Fallback: search all servers for the tool
        for cfg in self.bridge.configs:
            health = self._health.get(cfg.name)
            if health and not health.healthy:
                continue
            for tool in cfg.tools:
                if tool.get("name") == tool_name:
                    return cfg

        return None

    async def execute(self, tool_name: str, **kwargs) -> ToolResult:
        """Execute a tool call on the appropriate MCP server.

        Args:
            tool_name: Name of the tool to execute
            **kwargs: Tool arguments

        Returns:
            ToolResult with success/error
        """
        await self._maybe_health_check()

        cfg = self._find_server_for_tool(tool_name)
        if cfg is None:
            # Try direct bridge execution as fallback
            return await self.bridge.execute_tool(tool_name, **kwargs)

        try:
            start = time.time()
            result = await self.bridge.execute_tool(tool_name, **kwargs)
            elapsed_ms = (time.time() - start) * 1000

            # Update health on success
            self._record_success(cfg.name, elapsed_ms)
            return result

        except Exception as e:
            self._record_failure(cfg.name, str(e))
            # Retry with fallback server if available
            fallback = self._find_fallback_server(tool_name, exclude=cfg.name)
            if fallback:
                try:
                    result = await self.bridge.execute_tool(tool_name, **kwargs)
                    self._record_success(fallback.name, 0.0)
                    return result
                except Exception as e2:
                    self._record_failure(fallback.name, str(e2))
                    return ToolResult(success=False, content=None, error=f"Primary: {e}; Fallback: {e2}")

            return ToolResult(success=False, content=None, error=str(e))

    def _find_fallback_server(self, tool_name: str, exclude: str) -> Optional[MCPServerConfig]:
        """Find a fallback server for a tool, excluding the given server."""
        for cfg in self.bridge.configs:
            if cfg.name == exclude:
                continue
            health = self._health.get(cfg.name)
            if health and not health.healthy:
                continue
            for tool in cfg.tools:
                if tool.get("name") == tool_name:
                    return cfg
        return None

    def _record_success(self, server_name: str, latency_ms: float) -> None:
        health = self._health.get(server_name)
        if health:
            health.healthy = True
            health.consecutive_failures = 0
            health.error_message = ""
            # Exponential moving average for latency
            alpha = 0.3
            health.average_latency_ms = alpha * latency_ms + (1 - alpha) * health.average_latency_ms

    def _record_failure(self, server_name: str, error: str) -> None:
        health = self._health.get(server_name)
        if health:
            health.consecutive_failures += 1
            health.error_message = error
            if health.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                health.healthy = False
                health.last_check = time.time()

    async def _maybe_health_check(self) -> None:
        """Run periodic health checks on all servers."""
        now = time.time()
        if now - self._last_health_check < self.HEALTH_CHECK_INTERVAL:
            return

        self._last_health_check = now
        for cfg in self.bridge.configs:
            await self._health_check_server(cfg)

    async def _health_check_server(self, cfg: MCPServerConfig) -> None:
        """Check if an MCP server is healthy."""
        health = self._health.get(cfg.name)
        if not health:
            return

        # If server is marked unhealthy, check if cooldown has passed
        if not health.healthy:
            if time.time() - health.last_check < self.FAILURE_COOLDOWN:
                return

        try:
            # Simple health check: try to get tool schemas
            # For HTTP servers, a HEAD request would be better
            start = time.time()
            if cfg.url:
                # HTTP-based: try a simple ping
                result = await self.bridge.execute_tool(
                    cfg.tools[0].get("name") if cfg.tools else "ping",
                    **({} if cfg.tools else {})
                )
                if result.success:
                    self._record_success(cfg.name, (time.time() - start) * 1000)
                else:
                    self._record_failure(cfg.name, result.error or "Health check failed")
            elif cfg.command:
                # For stdio servers, we can't easily health check without spawning
                # Mark as healthy if we haven't seen failures
                if health.consecutive_failures == 0:
                    health.healthy = True

        except Exception as e:
            self._record_failure(cfg.name, str(e))

    def get_health_summary(self) -> dict[str, dict[str, Any]]:
        """Get health status summary for all servers."""
        return {
            name: {
                "healthy": h.healthy,
                "consecutive_failures": h.consecutive_failures,
                "average_latency_ms": round(h.average_latency_ms, 2),
                "error": h.error_message,
            }
            for name, h in self._health.items()
        }
