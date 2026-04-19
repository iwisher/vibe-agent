"""OpenAI-compatible API adapter.

Works with Ollama, vLLM, Applesay, Kimi, OpenRouter, and any other
OpenAI-compatible endpoint.
"""

from typing import Any, Dict, List, Optional, Tuple

from vibe.adapters.base import BaseLLMAdapter
from vibe.core.llm_types import LLMResponse


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for OpenAI-compatible /v1/chat/completions APIs."""

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
        url = f"{base_url.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        return url, headers, payload

    def parse_response(self, response_json: Dict[str, Any]) -> LLMResponse:
        choices = response_json.get("choices") or [{}]
        choice = choices[0]
        message = choice.get("message", {})

        return LLMResponse(
            content=message.get("content") or "",
            usage=response_json.get(
                "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            ),
            finish_reason=choice.get("finish_reason"),
            tool_calls=message.get("tool_calls"),
        )

    def health_check_endpoints(self, base_url: str, model_id: str) -> List[Tuple[str, str]]:
        return [
            ("GET", f"{base_url.rstrip('/')}/v1/models"),
            ("POST", f"{base_url.rstrip('/')}/v1/chat/completions"),
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
        """OpenAI uses role=system inside messages array. No extraction needed."""
        return None, list(messages)
