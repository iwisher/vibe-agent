"""Base adapter interface for LLM providers."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from vibe.core.llm_types import LLMResponse


class BaseLLMAdapter(ABC):
    """Abstract base for LLM API adapters.

    Adapters handle provider-specific request building, response parsing,
    health checks, and message format conversion.
    """

    @abstractmethod
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
        """Build an API request.

        Returns:
            (url, headers, json_payload)
        """
        ...

    @abstractmethod
    def parse_response(self, response_json: Dict[str, Any]) -> "LLMResponse":
        """Parse provider-specific JSON response into standardized LLMResponse."""
        ...

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
        """Build an API request configured for streaming.

        Defaults to calling build_request and appending stream=True to the payload.
        Override if the provider uses a different streaming endpoint or payload shape.
        """
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
        json_payload["stream"] = True
        return url, headers, json_payload

    def parse_stream_chunk(self, chunk_json: Dict[str, Any]) -> Optional["LLMResponse"]:
        """Parse provider-specific SSE stream chunk JSON into standardized LLMResponse.

        chunk_json is the parsed JSON dict from the SSE data line.
        Return None for non-data chunks or the [DONE] sentinel.

        Adapters supporting streaming must override this method.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support streaming.")

    @abstractmethod
    def health_check_endpoints(self, base_url: str, model_id: str) -> List[Tuple[str, str]]:
        """Return health-check probes as (method, url) tuples, in priority order.

        Methods are "GET" or "POST". The checker executes each probe
        in order until one succeeds.
        """
        ...

    @abstractmethod
    def parse_health_response(
        self, endpoint_method: str, endpoint_url: str, response_json: Dict[str, Any]
    ) -> bool:
        """Return True if the health probe indicates the model is available."""
        ...

    @abstractmethod
    def extract_system_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Extract system message content from messages array.

        Returns:
            (system_content, remaining_messages)
            For Anthropic: extracts role=system into top-level param.
            For OpenAI: returns (None, messages) unchanged.
        """
        ...

    def prepare_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Default message preparation: extract system messages."""
        return self.extract_system_messages(messages)
