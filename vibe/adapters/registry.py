"""Adapter registry for discovering available LLM adapters."""

from typing import Dict, Type

from vibe.adapters.base import BaseLLMAdapter
from vibe.adapters.openai import OpenAIAdapter
from vibe.adapters.anthropic import AnthropicAdapter

ADAPTER_REGISTRY: Dict[str, Type[BaseLLMAdapter]] = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
}


def get_adapter(name: str) -> Type[BaseLLMAdapter]:
    """Look up an adapter class by name.

    Raises:
        KeyError: If the adapter name is not registered.
    """
    if name not in ADAPTER_REGISTRY:
        raise KeyError(
            f"Unknown adapter '{name}'. Available: {list(ADAPTER_REGISTRY.keys())}"
        )
    return ADAPTER_REGISTRY[name]
