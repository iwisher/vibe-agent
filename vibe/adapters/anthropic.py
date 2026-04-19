"""Anthropic-native API adapter.

Supports Anthropic's Messages API (/v1/messages).
Also works with proxies that expose Anthropic-compatible endpoints
(e.g., Kimi coding endpoint when used with Anthropic SDK format).
"""

from typing import Any, Dict, List, Optional, Tuple

from vibe.adapters.base import BaseLLMAdapter
from vibe.core.model_gateway import LLMResponse


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
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = f"{base_url.rstrip("/")}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        system_content, remaining_messages = self.extract_system_messages(messages)

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
            payload["tool_choice"] = {"type": tool_choice} if tool_choice != "auto" else {"type": "auto"}

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
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": block.get("input", {}),
                    },
                })

        usage = response_json.get("usage", {})
        return LLMResponse(
            content="\n".join(text_parts),
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            finish_reason=response_json.get("stop_reason"),
            tool_calls=tool_calls if tool_calls else None,
        )

    def health_check_endpoints(self, base_url: str, model_id: str) -> List[str]:
        return [
            f"{base_url.rstrip("/")}/v1/models",
            f"{base_url.rstrip("/")}/v1/messages",
        ]

    def parse_health_response(self, endpoint: str, response_json: Dict[str, Any]) -> bool:
        if "/v1/models" in endpoint:
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
                converted.append({
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                converted.append(tool)
        return converted
