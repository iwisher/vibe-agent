"""Anthropic-native API adapter.

Supports Anthropic's Messages API (/v1/messages).
Also works with proxies that expose Anthropic-compatible endpoints
(e.g., Kimi coding endpoint when used with Anthropic SDK format).
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from vibe.adapters.base import BaseLLMAdapter
from vibe.core.llm_types import LLMResponse


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for Anthropic Messages API."""

    def build_request(
        self,
        base_url: str,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        api_key: Optional[str] = None,
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key

        system_content, remaining_messages = self.extract_system_messages(messages)
        if not remaining_messages:
            remaining_messages = [{"role": "user", "content": "Hello"}]
        elif remaining_messages[0].get("role") != "user":
            remaining_messages = [
                {"role": "user", "content": "Please continue the task."}
            ] + remaining_messages

        payload: Dict[str, Any] = {
            "model": model,
            "messages": remaining_messages,
            "temperature": temperature,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
        }
        if system_content:
            payload["system"] = system_content
        if tools:
            payload["tools"] = self._convert_tools(tools)
            payload["tool_choice"] = self._map_tool_choice(tool_choice)

        return url, headers, payload

    def parse_response(self, response_json: Dict[str, Any]) -> LLMResponse:
        content_blocks = response_json.get("content", [])
        text_parts = []
        tool_calls = []

        for block in content_blocks:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                # ... existing logic ...
                tool_input = block.get("input", {})
                arguments = (
                    json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input)
                )
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": arguments,
                        },
                    }
                )

        usage = response_json.get("usage", {})
        resp = LLMResponse(
            content="\n".join(text_parts),
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            finish_reason=response_json.get("stop_reason"),
            tool_calls=tool_calls if tool_calls else None,
        )
        return resp

    def parse_stream_chunk(self, chunk_json: Dict[str, Any]) -> Optional[LLMResponse]:
        if not chunk_json:
            return None
        if chunk_json == "[DONE]":
            return None
        if isinstance(chunk_json, dict) and chunk_json.get("done") is True:
            return None

        event_type = chunk_json.get("type")
        if not event_type or event_type in ("ping", "content_block_stop", "message_stop"):
            return None

        content = ""
        reasoning_content = ""
        tool_calls = None
        finish_reason = None
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if event_type == "content_block_delta":
            delta = chunk_json.get("delta", {})
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                content = delta.get("text") or ""
            elif delta_type == "thinking_delta":
                reasoning_content = delta.get("thinking") or ""
            elif delta_type == "input_json_delta":
                index = chunk_json.get("index", 0)
                tool_calls = [
                    {"index": index, "function": {"arguments": delta.get("partial_json") or ""}}
                ]
        elif event_type == "content_block_start":
            block = chunk_json.get("content_block", {})
            block_type = block.get("type")
            if block_type == "tool_use":
                index = chunk_json.get("index", 0)
                tool_calls = [
                    {
                        "index": index,
                        "id": block.get("id"),
                        "type": "function",
                        "function": {"name": block.get("name"), "arguments": ""},
                    }
                ]
        elif event_type == "message_delta":
            delta = chunk_json.get("delta", {})
            finish_reason = delta.get("stop_reason")
            usage_data = chunk_json.get("usage", {})
            if usage_data:
                out_tokens = usage_data.get("output_tokens", 0)
                usage = {
                    "prompt_tokens": 0,
                    "completion_tokens": out_tokens,
                    "total_tokens": out_tokens,
                }
        elif event_type == "message_start":
            msg = chunk_json.get("message", {})
            usage_data = msg.get("usage", {})
            if usage_data:
                in_tokens = usage_data.get("input_tokens", 0)
                usage = {
                    "prompt_tokens": in_tokens,
                    "completion_tokens": 0,
                    "total_tokens": in_tokens,
                }

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    def health_check_endpoints(self, base_url: str, model_id: str) -> List[Tuple[str, str]]:
        return [
            ("GET", f"{base_url.rstrip('/')}/v1/models"),
            ("POST", f"{base_url.rstrip('/')}/v1/messages"),
        ]

    def parse_health_response(
        self, endpoint_method: str, endpoint_url: str, response_json: Dict[str, Any]
    ) -> bool:
        if "/v1/models" in endpoint_url:
            models = response_json.get("data", [])
            return len(models) > 0
        return True

    def extract_system_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Anthropic uses top-level `system` param, not role=system in messages.

        Extracts all system messages, concatenates them, and returns
        the remaining non-system messages.
        """
        system_parts = []
        remaining = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(str(msg.get("content", "")))
            else:
                remaining.append(msg)
        system_content = "\n\n".join(system_parts) if system_parts else None
        return system_content, remaining

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tool definitions to Anthropic format.

        Anthropic tools use `input_schema` instead of `parameters`.
        """
        converted = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                converted.append(
                    {
                        "name": func.get("name"),
                        "description": func.get("description", ""),
                        "input_schema": func.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            else:
                converted.append(tool)
        return converted

    def _map_tool_choice(self, tool_choice: str) -> Dict[str, Any]:
        """Map OpenAI-style tool_choice to Anthropic format.

        OpenAI: "auto" | "none" | "required" | {"type": "function", "function": {"name": "..."}}
        Anthropic: {"type": "auto"} | {"type": "any"} | {"type": "tool", "name": "..."}
        """
        if tool_choice == "none":
            return {"type": "none"}
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice == "auto":
            return {"type": "auto"}
        # If it's a dict, assume it's already Anthropic-format or OpenAI function-specific
        if isinstance(tool_choice, dict):
            if tool_choice.get("type") == "function":
                func_name = tool_choice.get("function", {}).get("name")
                if func_name:
                    return {"type": "tool", "name": func_name}
            return tool_choice
        # Fallback
        return {"type": "auto"}
