"""LLM client for communicating with the local LLM server."""

import json
import httpx
from typing import Dict, Any, Iterator, Optional, List
from dataclasses import dataclass
from enum import Enum, auto


class ErrorType(Enum):
    """Types of errors that can occur during LLM communication."""
    NONE = auto()
    HTTP_ERROR = auto()
    JSON_DECODE_ERROR = auto()
    NETWORK_ERROR = auto()
    CONNECTION_ERROR = auto()
    TIMEOUT_ERROR = auto()
    RATE_LIMIT_ERROR = auto()
    AUTHENTICATION_ERROR = auto()
    SERVER_ERROR = auto()
    UNKNOWN_ERROR = auto()


class ErrorAction(Enum):
    """Recommended actions for error recovery."""
    NONE = auto()
    RETRY = auto()
    RETRY_WITH_BACKOFF = auto()
    CHECK_CREDENTIALS = auto()
    CHECK_NETWORK = auto()
    CHECK_SERVER_STATUS = auto()
    REDUCE_CONTEXT = auto()
    ABORT = auto()


@dataclass
class LLMResponse:
    """Response from LLM."""
    content: str
    usage: Dict[str, int]
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    error_type: ErrorType = ErrorType.NONE
    error_action: ErrorAction = ErrorAction.NONE
    tool_calls: Optional[List[Dict[str, Any]]] = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


class LLMClient:
    """Client for LLM API."""

    def __init__(self, base_url: str = "http://ai-api.applesay.cn", model: str = "qwen3.5-plus", api_key: Optional[str] = "sk-WAEUwVx1GmT3C2CREbBc2fD53fEf4dB6A373773d28CfAfA6"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = httpx.Client(timeout=300.0)
        
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with optional API key."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def get_headers_preview(self) -> str:
        """Get a preview of headers for debugging."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            key_preview = self.api_key[:10] + "..." if len(self.api_key) > 10 else self.api_key
            headers["Authorization"] = f"Bearer {key_preview}"
        return str(headers)

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Send completion request (alias for chat_completion)."""
        return self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            tools=tools,
        )

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Send chat completion request."""
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            headers = self._get_headers()
            response = self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

            # Extract content
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            finish_reason = choice.get("finish_reason")

            # Extract tool_calls if present
            tool_calls = message.get("tool_calls")

            # Extract usage
            usage = data.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

            return LLMResponse(
                content=content,
                usage=usage,
                finish_reason=finish_reason,
                tool_calls=tool_calls
            )

        except httpx.TimeoutException as e:
            return LLMResponse(
                content="",
                usage={},
                error=f"Request timeout: {str(e)}",
                error_type=ErrorType.TIMEOUT_ERROR,
                error_action=ErrorAction.RETRY_WITH_BACKOFF
            )
        except httpx.NetworkError as e:
            return LLMResponse(
                content="",
                usage={},
                error=f"Network error: {str(e)}",
                error_type=ErrorType.NETWORK_ERROR,
                error_action=ErrorAction.CHECK_NETWORK
            )
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 401 or status_code == 403:
                return LLMResponse(
                    content="",
                    usage={},
                    error=f"Authentication error ({status_code}): {str(e)}",
                    error_type=ErrorType.AUTHENTICATION_ERROR,
                    error_action=ErrorAction.CHECK_CREDENTIALS
                )
            elif status_code == 429:
                return LLMResponse(
                    content="",
                    usage={},
                    error=f"Rate limit exceeded (429): {str(e)}",
                    error_type=ErrorType.RATE_LIMIT_ERROR,
                    error_action=ErrorAction.RETRY_WITH_BACKOFF
                )
            elif status_code >= 500:
                return LLMResponse(
                    content="",
                    usage={},
                    error=f"Server error ({status_code}): {str(e)}",
                    error_type=ErrorType.SERVER_ERROR,
                    error_action=ErrorAction.CHECK_SERVER_STATUS
                )
            else:
                return LLMResponse(
                    content="",
                    usage={},
                    error=f"HTTP error ({status_code}): {str(e)}",
                    error_type=ErrorType.HTTP_ERROR,
                    error_action=ErrorAction.RETRY
                )
        except httpx.HTTPError as e:
            return LLMResponse(
                content="",
                usage={},
                error=f"HTTP error: {str(e)}",
                error_type=ErrorType.HTTP_ERROR,
                error_action=ErrorAction.RETRY
            )
        except json.JSONDecodeError as e:
            return LLMResponse(
                content="",
                usage={},
                error=f"JSON decode error: {str(e)}",
                error_type=ErrorType.JSON_DECODE_ERROR,
                error_action=ErrorAction.RETRY
            )
        except Exception as e:
            return LLMResponse(
                content="",
                usage={},
                error=f"Error: {str(e)}",
                error_type=ErrorType.UNKNOWN_ERROR,
                error_action=ErrorAction.ABORT
            )

    def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        """Stream chat completion response."""
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            headers = self._get_headers()
            with self.client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                yield delta
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"[Error: {str(e)}]"

    def set_model(self, model: str) -> None:
        """Set the model to use."""
        self.model = model
    
    def get_model(self) -> str:
        """Get the current model."""
        return self.model
    
    def list_models(self) -> List[Dict[str, Any]]:
        """List available models from the API."""
        url = f"{self.base_url}/v1/models"
        try:
            headers = self._get_headers()
            response = self.client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except Exception as e:
            return [{"error": str(e)}]

    def close(self):
        """Close the HTTP client."""
        self.client.close()