"""Provider registry for multi-provider LLM support.

A provider is a connection endpoint (e.g., Ollama, Kimi, Anthropic, OpenRouter).
Each provider has its own base_url, auth, adapter format, and timeout.
Models are evaluated against providers via the ModelRegistry (Phase D/E).
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from vibe.adapters.base import BaseLLMAdapter
from vibe.adapters.registry import ADAPTER_REGISTRY, get_adapter
from vibe.core.llm_types import LLMResponse
from vibe.core.model_gateway import LLMClient


@dataclass
class ProviderProfile:
    """Connection profile for a single LLM provider endpoint."""

    name: str
    base_url: str
    adapter_type: str = "openai"
    api_key: Optional[str] = None
    api_key_env_var: Optional[str] = "LLM_API_KEY"
    timeout: float = 120.0
    default_model: Optional[str] = None
    # Cost-aware routing (Phase 3.3)
    cost_tier: str = "standard"  # free | budget | standard | premium | ultra
    max_context_tokens: int = 8000
    cost_per_1k_prompt: float = 0.0
    cost_per_1k_completion: float = 0.0
    # Extra headers injected on every request (e.g., OpenRouter routing)
    extra_headers: Dict[str, str] = field(default_factory=dict)

    def resolve_api_key(self) -> Optional[str]:
        """Resolve API key: explicit value > env var > None."""
        if self.api_key:
            return self.api_key
        if self.api_key_env_var:
            return os.getenv(self.api_key_env_var)
        return None

    def create_adapter(self) -> BaseLLMAdapter:
        """Instantiate the adapter for this provider's API format."""
        return get_adapter(self.adapter_type)()

    def create_client(self, model_id: Optional[str] = None, api_key: Optional[str] = None) -> LLMClient:
        """Create an LLMClient wired to this provider with the correct adapter."""
        resolved_key = api_key or self.resolve_api_key()
        adapter = self.create_adapter()
        return LLMClient(
            base_url=self.base_url,
            model=model_id or self.default_model or "default",
            api_key=resolved_key,
            timeout=self.timeout,
            adapter=adapter,
        )


class ProviderRegistry:
    """Registry of available LLM providers.

    Provides lookup by name and factory methods for creating LLMClients.
    """

    def __init__(self, providers: Optional[Dict[str, ProviderProfile]] = None):
        self._providers: Dict[str, ProviderProfile] = dict(providers) if providers else {}

    def get(self, name: str) -> Optional[ProviderProfile]:
        return self._providers.get(name)

    def register(self, profile: ProviderProfile) -> None:
        """Register or overwrite a provider profile."""
        self._providers[profile.name] = profile

    def remove(self, name: str) -> None:
        """Remove a provider from the registry."""
        self._providers.pop(name, None)

    def list_providers(self) -> List[str]:
        return list(self._providers.keys())

    def resolve_client(
        self,
        provider_name: str,
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> LLMClient:
        """Resolve a provider to a fully configured LLMClient.

        Raises:
            ValueError: If the provider is not registered.
        """
        provider = self.get(provider_name)
        if provider is None:
            raise ValueError(
                f"Unknown provider '{provider_name}'. "
                f"Registered: {', '.join(self.list_providers()) or '(none)'}"
            )
        return provider.create_client(model_id=model_id, api_key=api_key)

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "base_url": p.base_url,
                "adapter_type": p.adapter_type,
                "api_key_env_var": p.api_key_env_var,
                "timeout": p.timeout,
                "default_model": p.default_model,
                "cost_tier": p.cost_tier,
                "max_context_tokens": p.max_context_tokens,
                "cost_per_1k_prompt": p.cost_per_1k_prompt,
                "cost_per_1k_completion": p.cost_per_1k_completion,
                "extra_headers": p.extra_headers,
            }
            for name, p in self._providers.items()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Dict[str, Any]]) -> "ProviderRegistry":
        """Build a ProviderRegistry from a plain dict (e.g., loaded from YAML)."""
        providers = {}
        for name, cfg in data.items():
            providers[name] = ProviderProfile(
                name=name,
                base_url=cfg["base_url"],
                adapter_type=cfg.get("adapter_type", "openai"),
                api_key=cfg.get("api_key"),
                api_key_env_var=cfg.get("api_key_env_var", "LLM_API_KEY"),
                timeout=float(cfg.get("timeout", 120.0)),
                default_model=cfg.get("default_model"),
                cost_tier=cfg.get("cost_tier", "standard"),
                max_context_tokens=int(cfg.get("max_context_tokens", 8000)),
                cost_per_1k_prompt=float(cfg.get("cost_per_1k_prompt", 0.0)),
                cost_per_1k_completion=float(cfg.get("cost_per_1k_completion", 0.0)),
                extra_headers=cfg.get("extra_headers", {}),
            )
        return cls(providers)

    @classmethod
    def default_ollama(cls) -> "ProviderRegistry":
        """Convenience factory for a single Ollama provider at localhost."""
        return cls({
            "ollama": ProviderProfile(
                name="ollama",
                base_url=os.getenv("VIBE_BASE_URL", "http://localhost:11434"),
                adapter_type="openai",
                api_key_env_var="LLM_API_KEY",
            )
        })
