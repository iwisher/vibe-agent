"""Core LLM domain types shared across vibe-agent.

Extracted from model_gateway.py to avoid circular imports with adapters.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional


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
    MODEL_UNAVAILABLE = auto()


@dataclass
class LLMResponse:
    """Standardized response from LLM."""

    content: str
    usage: Dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    error_type: ErrorType = ErrorType.NONE
    actionable_hint: Optional[str] = None
    model_used: Optional[str] = None  # Actual model after fallback resolution

    @property
    def is_error(self) -> bool:
        return self.error is not None
