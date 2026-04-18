"""Model registry for multi-model benchmarking with fallback chains.

Supports applesay endpoints which proxy multiple models through
a single base_url with different model IDs.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set


@dataclass
class ModelProfile:
    """Configuration for a single model endpoint."""

    name: str
    provider: str
    base_url: str
    model_id: str
    api_key: Optional[str] = None
    api_key_env_var: str = "LLM_API_KEY"
    timeout: float = 120.0
    cost_per_1k_prompt: float = 0.0
    cost_per_1k_completion: float = 0.0
    tags: List[str] = None
    is_default: bool = False
    is_ci_model: bool = False

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.api_key is None:
            self.api_key = os.getenv(self.api_key_env_var)

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        return os.getenv(self.api_key_env_var)


class ModelRegistry:
    """Registry of available LLM models for eval benchmarking."""

    # Built-in profiles for applesay endpoint
    APPLEsay_BASE = "http://ai-api.applesay.cn"

    BUILTIN_PROFILES: List[ModelProfile] = [
        ModelProfile(
            name="qwen3.5-plus",
            provider="applesay",
            base_url=APPLEsay_BASE,
            model_id="qwen3.5-plus",
            api_key_env_var="APPLEsay_API_KEY",
            cost_per_1k_prompt=0.0005,
            cost_per_1k_completion=0.0015,
            tags=["cheap", "fast", "reliable"],
            is_ci_model=True,
        ),
        ModelProfile(
            name="kimi-k2.5",
            provider="applesay",
            base_url=APPLEsay_BASE,
            model_id="kimi-k2.5",
            api_key_env_var="APPLEsay_API_KEY",
            cost_per_1k_prompt=0.002,
            cost_per_1k_completion=0.006,
            tags=["coding", "reasoning", "high-quality"],
        ),
        ModelProfile(
            name="minimax-m2.5",
            provider="applesay",
            base_url=APPLEsay_BASE,
            model_id="MiniMax-M2.5",
            api_key_env_var="APPLEsay_API_KEY",
            cost_per_1k_prompt=0.001,
            cost_per_1k_completion=0.003,
            tags=["balanced", "multilingual"],
            is_default=True,
            is_ci_model=True,
        ),
        ModelProfile(
            name="minimax-m2.7",
            provider="applesay",
            base_url=APPLEsay_BASE,
            model_id="MiniMax-M2.7",
            api_key_env_var="APPLEsay_API_KEY",
            cost_per_1k_prompt=0.0015,
            cost_per_1k_completion=0.0045,
            tags=["balanced", "multilingual", "large-context"],
        ),
        ModelProfile(
            name="qwen3-max",
            provider="applesay",
            base_url=APPLEsay_BASE,
            model_id="qwen3-max-2026-01-23",
            api_key_env_var="APPLEsay_API_KEY",
            cost_per_1k_prompt=0.001,
            cost_per_1k_completion=0.003,
            tags=["reasoning", "long-context"],
        ),
        ModelProfile(
            name="glm-5",
            provider="applesay",
            base_url=APPLEsay_BASE,
            model_id="glm-5",
            api_key_env_var="APPLEsay_API_KEY",
            cost_per_1k_prompt=0.001,
            cost_per_1k_completion=0.003,
            tags=["reasoning", "chinese-optimized"],
        ),
    ]

    def __init__(self, profiles: Optional[List[ModelProfile]] = None):
        import copy
        self._profiles: Dict[str, ModelProfile] = {}
        source = profiles if profiles is not None else self.BUILTIN_PROFILES
        for p in source:
            # Deep copy to prevent mutations on shared class-level state
            self._profiles[p.name] = copy.deepcopy(p)

    def get(self, name: str) -> Optional[ModelProfile]:
        return self._profiles.get(name)

    def list_models(self, tag: Optional[str] = None) -> List[str]:
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

    def get_fallback_chain(self, primary: str, config: Optional[Any] = None) -> List[ModelProfile]:
        """Return fallback chain: primary → config chain → compatible alternatives → default."""
        chain: List[ModelProfile] = []
        seen: set = set()

        # Primary
        primary_profile = self.get(primary)
        if primary_profile:
            chain.append(primary_profile)
            seen.add(primary_profile.name)

        # Config-specified fallback chain (if provided)
        if config is not None:
            config_chain = config.get_fallback_chain()
            for name in config_chain:
                if name == primary:
                    continue
                p = self.get(name)
                if p and p.name not in seen:
                    chain.append(p)
                    seen.add(p.name)
            return chain if chain else [primary_profile] if primary_profile else []

        # Same-provider fallbacks (legacy behavior when no config)
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

    def to_dict(self) -> Dict[str, dict]:
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
