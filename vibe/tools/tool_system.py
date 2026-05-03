import abc
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    """Base exception for tool execution errors."""
    pass

@dataclass
class ToolResult:
    success: bool
    content: Any
    error: str | None = None

class Tool(abc.ABC):
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abc.abstractmethod
    def get_schema(self) -> dict[str, Any]:
        """Return OpenAI-style function schema."""
        pass

    @abc.abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool logic."""
        pass

class ToolSystem:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register_tool(self, tool: Tool):
        self._tools[tool.name] = tool

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for tool in self._tools.values():
            schema = tool.get_schema()
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                },
            })
        return schemas

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    async def execute_tool(self, tool_name: str, **kwargs) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(success=False, content=None, error=f"Tool '{tool_name}' not found")

        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))
