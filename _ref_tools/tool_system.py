"""Tool system for managing and executing tools."""

import asyncio
import inspect
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable
from enum import Enum


class ToolError(Exception):
    """Error executing a tool."""
    pass


@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    content: Any
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "error": self.error
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class Tool(ABC):
    """Base class for tools."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    def get_schema(self) -> Dict[str, Any]:
        """Return the tool schema for LLM."""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters."""
        pass

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters before execution."""
        return True, None


class ToolSystem:
    """System for managing and executing tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._permission_gate = None

    def register_tool(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def register_tools(self, tools: List[Tool]) -> None:
        """Register multiple tools."""
        for tool in tools:
            self.register_tool(tool)

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get schemas for all registered tools."""
        schemas = []
        for tool in self._tools.values():
            schema = tool.get_schema()
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema
                }
            })
        return schemas

    async def execute_tool(self, name: str, **kwargs) -> ToolResult:
        """Execute a tool by name (async)."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                content=None,
                error=f"Tool '{name}' not found"
            )

        # Validate parameters
        valid, error = tool.validate_params(kwargs)
        if not valid:
            return ToolResult(success=False, content=None, error=error)

        # Execute the tool
        try:
            if asyncio.iscoroutinefunction(tool.execute):
                return await tool.execute(**kwargs)
            else:
                return tool.execute(**kwargs)
        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Execution error: {str(e)}"
            )

    def execute_from_llm_call(self, tool_calls: List[Dict[str, Any]]) -> List[ToolResult]:
        """Execute tools from LLM tool calls."""
        results = []
        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            arguments = json.loads(call.get("function", {}).get("arguments", "{}"))
            result = self.execute_tool(name, **arguments)
            results.append(result)
        return results