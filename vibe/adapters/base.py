"""Base adapter interface for LLM providers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


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
