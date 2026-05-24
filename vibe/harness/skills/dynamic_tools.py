"""Dynamic tool declaration from skills.

Allows skills to declare new tools at runtime, extending the harness's
registered tool set without code changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class DynamicTool:
    """A tool dynamically declared by a skill."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Callable | None = None
    skill_source: str = ""  # Which skill declared this tool

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema for LLM tool calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class DynamicToolRegistry:
    """Registry for tools dynamically declared by skills.

    Skills can declare new tools at runtime, which are then available
    to the LLM for the duration of the session.
    """

    def __init__(self) -> None:
        self._tools: dict[str, DynamicTool] = {}
        self._handlers: dict[str, Callable] = {}

    def register(
        self,
        tool: DynamicTool,
        handler: Callable | None = None,
    ) -> bool:
        """Register a dynamic tool.

        Args:
            tool: The tool definition
            handler: Optional handler function (can be set later)

        Returns:
            True if registered, False if name collision
        """
        if tool.name in self._tools:
            logger.warning(
                f"Dynamic tool '{tool.name}' already registered (from {self._tools[tool.name].skill_source})"
            )
            return False

        self._tools[tool.name] = tool
        if handler:
            self._handlers[tool.name] = handler
        logger.info(f"Registered dynamic tool '{tool.name}' from skill '{tool.skill_source}'")
        return True

    def unregister(self, name: str) -> bool:
        """Unregister a dynamic tool."""
        if name in self._tools:
            del self._tools[name]
            self._handlers.pop(name, None)
            logger.info(f"Unregistered dynamic tool '{name}'")
            return True
        return False

    def get_tool(self, name: str) -> DynamicTool | None:
        """Get a dynamic tool by name."""
        return self._tools.get(name)

    def get_handler(self, name: str) -> Callable | None:
        """Get the handler for a dynamic tool."""
        return self._handlers.get(name)

    def set_handler(self, name: str, handler: Callable) -> bool:
        """Set/update the handler for a dynamic tool."""
        if name not in self._tools:
            return False
        self._handlers[name] = handler
        return True

    def list_tools(self) -> list[DynamicTool]:
        """List all registered dynamic tools."""
        return list(self._tools.values())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas for LLM consumption."""
        return [t.to_json_schema() for t in self._tools.values()]

    def clear(self) -> None:
        """Clear all dynamic tools."""
        self._tools.clear()
        self._handlers.clear()

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


class SkillToolDeclarator:
    """Allows skills to declare tools via structured metadata.

    Skills can include a `tools` section in their frontmatter:
    ```toml
    [tools.my_tool]
    description = "Does something useful"
    parameters = { type = "object", properties = { ... } }
    ```
    """

    def __init__(self, registry: DynamicToolRegistry) -> None:
        self.registry = registry

    def declare_from_skill(self, skill_name: str, tools_meta: list[dict[str, Any]]) -> list[str]:
        """Declare tools from skill metadata.

        Args:
            skill_name: Name of the declaring skill
            tools_meta: List of tool metadata dicts

        Returns:
            List of registered tool names
        """
        registered = []
        for meta in tools_meta:
            tool = DynamicTool(
                name=meta["name"],
                description=meta.get("description", ""),
                parameters=meta.get("parameters", {"type": "object", "properties": {}}),
                skill_source=skill_name,
            )
            if self.registry.register(tool):
                registered.append(tool.name)
        return registered

    def create_handler_wrapper(
        self,
        tool_name: str,
        skill_executor: Any,
        skill: Any,
    ) -> Callable:
        """Create a handler that routes tool calls back to the skill.

        This allows the skill to handle its own dynamic tool invocations.
        """
        def handler(**kwargs):
            logger.debug(f"Dynamic tool '{tool_name}' called with {kwargs}")
            # Route to skill's execution context
            if hasattr(skill_executor, "handle_dynamic_tool"):
                return skill_executor.handle_dynamic_tool(tool_name, kwargs)
            return {"error": f"No handler for dynamic tool '{tool_name}'"}

        return handler
