"""Internal utilities for tool call handling."""

from typing import Any


def extract_tool_call_name(call: Any) -> str | None:
    """Extract the tool name from a tool call, handling both dict and object formats.

    Supports OpenAI-style dict format:
      {"name": "..."} or {"function": {"name": "..."}}
    And object format with `.name` attribute.
    """
    if isinstance(call, dict):
        return call.get("name") or call.get("function", {}).get("name")
    return getattr(call, "name", None)


def extract_tool_call_arguments(call: Any) -> dict[str, Any]:
    """Extract the arguments from a tool call, handling both dict and object formats.

    Returns a dict. If the arguments are a JSON string, they are parsed.
    Raises json.JSONDecodeError if the string is not valid JSON.
    """
    import json

    if isinstance(call, dict):
        args = call.get("arguments") or call.get("function", {}).get("arguments", "{}")
    else:
        args = getattr(call, "arguments", "{}")

    if isinstance(args, str):
        return json.loads(args)
    return args if isinstance(args, dict) else {}


def extract_tool_call_id(call: Any) -> str | None:
    """Extract the tool call ID from a tool call."""
    if isinstance(call, dict):
        return call.get("id")
    return getattr(call, "id", None)
