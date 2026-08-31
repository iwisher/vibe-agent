"""Google Gemini native API adapter.

Supports Google's Generative Language API (/v1beta/models/{model}:generateContent
and /v1beta/models/{model}:streamGenerateContent).
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from vibe.adapters.base import BaseLLMAdapter
from vibe.core.llm_types import LLMResponse


class GeminiAdapter(BaseLLMAdapter):
    """Adapter for Google Gemini native REST API endpoints."""

    def _normalize_model_name(self, model: str) -> str:
        """Strip 'models/' prefix if present."""
        if model.startswith("models/"):
            return model[len("models/") :]
        return model

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
        norm_model = self._normalize_model_name(model)
        url = f"{base_url.rstrip('/')}/v1beta/models/{norm_model}:generateContent"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-goog-api-key"] = api_key

        system_content, remaining_messages = self.extract_system_messages(messages)

        payload: Dict[str, Any] = {
            "contents": self._convert_messages(remaining_messages),
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens

        if system_content:
            payload["system_instruction"] = {
                "parts": [{"text": system_content}],
            }

        if tools:
            payload["tools"] = self._convert_tools(tools)
            tool_config = self._map_tool_choice(tool_choice)
            if tool_config:
                payload["tool_config"] = tool_config

        return url, headers, payload

    def build_stream_request(
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
        url, headers, json_payload = self.build_request(
            base_url=base_url,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            api_key=api_key,
        )
        norm_model = self._normalize_model_name(model)
        stream_url = (
            f"{base_url.rstrip('/')}/v1beta/models/{norm_model}:streamGenerateContent?alt=sse"
        )
        return stream_url, headers, json_payload

    def parse_response(self, response_json: Dict[str, Any]) -> LLMResponse:
        candidates = response_json.get("candidates") or [{}]
        candidate = candidates[0] if candidates else {}
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])

        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for idx, part in enumerate(parts):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                args = fc.get("args", {})
                arguments_str = json.dumps(args) if isinstance(args, dict) else str(args)
                tool_calls.append(
                    {
                        "id": f"call_{idx}_{fc.get('name', 'tool')}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": arguments_str,
                        },
                    }
                )

        usage_meta = response_json.get("usageMetadata", {})
        prompt_tokens = usage_meta.get("promptTokenCount", 0)
        completion_tokens = usage_meta.get("candidatesTokenCount", 0)
        total_tokens = usage_meta.get("totalTokenCount", prompt_tokens + completion_tokens)

        raw_finish = candidate.get("finishReason")
        finish_reason = self._map_finish_reason(raw_finish)

        return LLMResponse(
            content="\n".join(text_parts),
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            finish_reason=finish_reason,
            tool_calls=tool_calls if tool_calls else None,
        )

    def parse_stream_chunk(self, chunk_json: Dict[str, Any]) -> Optional[LLMResponse]:
        if not chunk_json:
            return None
        if chunk_json == "[DONE]":
            return None
        if isinstance(chunk_json, dict) and chunk_json.get("done") is True:
            return None

        candidates = chunk_json.get("candidates")
        if not candidates:
            # Chunk may only contain usageMetadata
            usage_meta = chunk_json.get("usageMetadata")
            if usage_meta:
                prompt_tokens = usage_meta.get("promptTokenCount", 0)
                completion_tokens = usage_meta.get("candidatesTokenCount", 0)
                total_tokens = usage_meta.get("totalTokenCount", prompt_tokens + completion_tokens)
                return LLMResponse(
                    content="",
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                )
            return None

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])

        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for idx, part in enumerate(parts):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                args = fc.get("args", {})
                arguments_str = json.dumps(args) if isinstance(args, dict) else str(args)
                tool_calls.append(
                    {
                        "index": idx,
                        "id": f"call_{idx}_{fc.get('name', 'tool')}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": arguments_str,
                        },
                    }
                )

        usage_meta = chunk_json.get("usageMetadata")
        usage = None
        if usage_meta:
            prompt_tokens = usage_meta.get("promptTokenCount", 0)
            completion_tokens = usage_meta.get("candidatesTokenCount", 0)
            total_tokens = usage_meta.get("totalTokenCount", prompt_tokens + completion_tokens)
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

        raw_finish = candidate.get("finishReason")
        finish_reason = self._map_finish_reason(raw_finish)

        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=finish_reason,
            usage=usage,
        )

    def health_check_endpoints(self, base_url: str, model_id: str) -> List[Tuple[str, str]]:
        return [
            ("GET", f"{base_url.rstrip('/')}/v1beta/models"),
        ]

    def parse_health_response(
        self, endpoint_method: str, endpoint_url: str, response_json: Dict[str, Any]
    ) -> bool:
        if "/v1beta/models" in endpoint_url:
            models = response_json.get("models", [])
            return len(models) > 0
        return True

    def extract_system_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Extract system messages to top-level system_instruction."""
        system_parts = []
        remaining = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(str(msg.get("content", "")))
            else:
                remaining.append(msg)
        system_content = "\n\n".join(system_parts) if system_parts else None
        return system_content, remaining

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert standard OpenAI-style messages to Gemini contents array."""
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": str(content or "")}],
                    }
                )
            elif role == "assistant":
                parts: List[Dict[str, Any]] = []
                if content:
                    parts.append({"text": str(content)})

                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        func_name = func.get("name", "")
                        raw_args = func.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                args_dict = json.loads(raw_args)
                            except json.JSONDecodeError:
                                args_dict = {"raw_input": raw_args}
                        elif isinstance(raw_args, dict):
                            args_dict = raw_args
                        else:
                            args_dict = {}

                        parts.append(
                            {
                                "functionCall": {
                                    "name": func_name,
                                    "args": args_dict,
                                }
                            }
                        )

                if not parts:
                    parts.append({"text": ""})

                contents.append(
                    {
                        "role": "model",
                        "parts": parts,
                    }
                )
            elif role in ("tool", "function"):
                func_name = msg.get("name") or "tool_result"
                tool_content = content if content is not None else ""
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": func_name,
                                    "response": {
                                        "name": func_name,
                                        "content": tool_content,
                                    },
                                }
                            }
                        ],
                    }
                )

        return contents

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tool definitions to Gemini function_declarations."""
        declarations: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" or "function" in tool:
                func = tool.get("function", {})
                declarations.append(
                    {
                        "name": func.get("name"),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {"type": "object", "properties": {}}),
                    }
                )
            elif "name" in tool:
                declarations.append(tool)

        if not declarations:
            return []
        return [{"function_declarations": declarations}]

    def _map_tool_choice(self, tool_choice: str | Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map tool_choice to Gemini tool_config format."""
        if tool_choice == "auto":
            return {"function_calling_config": {"mode": "AUTO"}}
        if tool_choice == "none":
            return {"function_calling_config": {"mode": "NONE"}}
        if tool_choice == "required":
            return {"function_calling_config": {"mode": "ANY"}}
        if isinstance(tool_choice, dict):
            if tool_choice.get("type") == "function":
                func_name = tool_choice.get("function", {}).get("name")
                if func_name:
                    return {
                        "function_calling_config": {
                            "mode": "ANY",
                            "allowed_function_names": [func_name],
                        }
                    }
            return tool_choice
        return None

    def _map_finish_reason(self, raw_finish: Optional[str]) -> Optional[str]:
        """Map Gemini finishReason to standard finish reason."""
        if not raw_finish:
            return None
        mapping = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
            "OTHER": "stop",
        }
        return mapping.get(raw_finish, raw_finish.lower())
