"""Model registry for multi-model benchmarking with fallback chains.

Supports OpenAI-compatible endpoints (Ollama, vLLM, etc.) which proxy
multiple models through a single base_url with different model IDs.
"""

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelProfile:
    """Configuration for a single model endpoint."""

    name: str
    provider: str
    base_url: str
    model_id: str
    api_key: str | None = None
    api_key_env_var: str = "LLM_API_KEY"
    timeout: float = 120.0
    cost_per_1k_prompt: float = 0.0
    cost_per_1k_completion: float = 0.0
    tags: list[str] = None
    is_default: bool = False
    is_ci_model: bool = False

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.api_key is None:
            self.api_key = os.getenv(self.api_key_env_var)

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        return os.getenv(self.api_key_env_var)


class ModelRegistry:
    """Registry of available LLM models for eval benchmarking."""

    # Default base URL (Ollama local endpoint)
    DEFAULT_BASE_URL = os.getenv("VIBE_BASE_URL", "http://localhost:11434")

    BUILTIN_PROFILES: list[ModelProfile] = [
        ModelProfile(
            name="default",
            provider="ollama",
            base_url=DEFAULT_BASE_URL,
            model_id="default",
            api_key_env_var="LLM_API_KEY",
            cost_per_1k_prompt=0.0,
            cost_per_1k_completion=0.0,
            tags=["local", "free"],
            is_default=True,
            is_ci_model=True,
        ),
    ]

    def __init__(self, profiles: list[ModelProfile] | None = None):
        import copy
        self._profiles: dict[str, ModelProfile] = {}
        source = profiles if profiles is not None else self.BUILTIN_PROFILES
        for p in source:
            # Deep copy to prevent mutations on shared class-level state
            self._profiles[p.name] = copy.deepcopy(p)

    def get(self, name: str) -> ModelProfile | None:
        return self._profiles.get(name)

    def list_models(self, tag: str | None = None) -> list[str]:
        if tag is None:
            return list(self._profiles.keys())
        return [name for name, p in self._profiles.items() if tag in p.tags]

    def get_default(self) -> ModelProfile:
        for p in self._profiles.values():
            if p.is_default:
                return p
        return list(self._profiles.values())[0]

    def get_ci_model(self) -> ModelProfile:
        for p in self._profiles.values():
            if p.is_ci_model:
                return p
        return self.get_default()

    def get_fallback_chain(self, primary: str, config: Any | None = None) -> list[ModelProfile]:
        """Return fallback chain: primary → config chain → compatible alternatives → default."""
        chain: list[ModelProfile] = []
        seen: set = set()

        # Primary
        primary_profile = self.get(primary)
        if primary_profile:
            chain.append(primary_profile)
            seen.add(primary_profile.name)

        # Config-specified fallback chain (if provided)
        if config is not None:
            config_chain = config.get_fallback_chain()
            if config_chain:
                for name in config_chain:
                    if name == primary:
                        continue
                    p = self.get(name)
                    if p and p.name not in seen:
                        chain.append(p)
                        seen.add(p.name)
                return chain if chain else [primary_profile] if primary_profile else []

        # Same-provider fallbacks (legacy behavior when no config or empty chain)
        if primary_profile:
            for p in self._profiles.values():
                if p.provider == primary_profile.provider and p.name not in seen:
                    chain.append(p)
                    seen.add(p.name)

        # Default as last resort
        default = self.get_default()
        if default.name not in seen:
            chain.append(default)

        return chain

    def add_profile(self, profile: ModelProfile) -> None:
        self._profiles[profile.name] = profile

    @classmethod
    def from_config(cls, config) -> "ModelRegistry":
        """Build a ModelRegistry from VibeConfig, linking models to providers.

        Uses config.models (raw dict) and config.providers (ProviderRegistry)
        to populate ModelProfiles with correct base_url, api_key, and adapter.
        """
        from vibe.core.config import VibeConfig

        if not isinstance(config, VibeConfig):
            raise TypeError(f"Expected VibeConfig, got {type(config).__name__}")

        profiles: list[ModelProfile] = []
        raw_models = config.models or {}

        for name, model_cfg in raw_models.items():
            if not isinstance(model_cfg, dict):
                continue
            provider_name = model_cfg.get("provider", "default")
            provider_reg = getattr(config, "providers", None)
            provider = provider_reg.get(provider_name) if provider_reg is not None else None

            if provider is not None:
                base_url = provider.base_url
                api_key = provider.resolve_api_key()
                api_key_env_var = provider.api_key_env_var or "LLM_API_KEY"
                timeout = provider.timeout
            else:
                base_url = model_cfg.get("base_url", config.llm.base_url)
                api_key = model_cfg.get("api_key")
                api_key_env_var = model_cfg.get("api_key_env_var", "LLM_API_KEY")
                timeout = model_cfg.get("timeout", config.llm.timeout)

            profiles.append(
                ModelProfile(
                    name=name,
                    provider=provider_name,
                    base_url=base_url,
                    model_id=model_cfg.get("model_id", name),
                    api_key=api_key,
                    api_key_env_var=api_key_env_var,
                    timeout=float(timeout),
                    cost_per_1k_prompt=model_cfg.get("cost_per_1k_prompt", 0.0),
                    cost_per_1k_completion=model_cfg.get("cost_per_1k_completion", 0.0),
                    tags=model_cfg.get("tags", []),
                    is_default=model_cfg.get("is_default", False),
                    is_ci_model=model_cfg.get("is_ci_model", False),
                )
            )

        # If no models configured, fall back to built-in profiles
        if not profiles:
            return cls()
        return cls(profiles)

    def to_dict(self) -> dict[str, dict]:
        return {
            name: {
                "provider": p.provider,
                "base_url": p.base_url,
                "model_id": p.model_id,
                "api_key_env_var": p.api_key_env_var,
                "timeout": p.timeout,
                "cost_per_1k_prompt": p.cost_per_1k_prompt,
                "cost_per_1k_completion": p.cost_per_1k_completion,
                "tags": p.tags,
                "is_default": p.is_default,
                "is_ci_model": p.is_ci_model,
            }
            for name, p in self._profiles.items()
        }
