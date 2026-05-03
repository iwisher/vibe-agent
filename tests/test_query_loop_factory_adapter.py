"""Tests for QueryLoopFactory adapter wiring (Phase D)."""

import pytest

from vibe.core.config import VibeConfig
from vibe.core.provider_registry import ProviderProfile
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.evals.model_registry import ModelProfile, ModelRegistry


class TestQueryLoopFactoryAdapter:
    def test_create_llm_with_adapter_type(self):
        factory = QueryLoopFactory(
            base_url="https://api.kimi.com/coding",
            model="claude-sonnet",
            adapter_type="anthropic",
        )
        llm = factory.create_llm()
        # Verify the adapter was set
        assert llm.adapter is not None
        from vibe.adapters.anthropic import AnthropicAdapter
        assert isinstance(llm.adapter, AnthropicAdapter)

    def test_create_llm_without_adapter_type_defaults_to_openai(self):
        factory = QueryLoopFactory(
            base_url="http://localhost:11434",
            model="llama3.2",
        )
        llm = factory.create_llm()
        from vibe.adapters.openai import OpenAIAdapter
        assert isinstance(llm.adapter, OpenAIAdapter)

    def test_from_profile_resolves_adapter_from_provider_registry(self):
        config = VibeConfig.load(auto_create=False)
        config.providers.register(
            ProviderProfile(name="kimi", base_url="https://api.kimi.com/coding", adapter_type="anthropic")
        )
        profile = ModelProfile(
            name="kimi-sonnet",
            provider="kimi",
            base_url="https://api.kimi.com/coding",
            model_id="claude-sonnet",
        )
        factory = QueryLoopFactory.from_profile(profile, config=config)
        assert factory.adapter_type == "anthropic"
        llm = factory.create_llm()
        from vibe.adapters.anthropic import AnthropicAdapter
        assert isinstance(llm.adapter, AnthropicAdapter)

    def test_create_llm_propagates_circuit_breaker_settings(self, tmp_path):
        import yaml
        config_data = {
            "fallback": {
                "circuit_breaker_threshold": 3,
                "circuit_breaker_cooldown": 120.0,
            }
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")
        config = VibeConfig.load(path=config_path, auto_create=False)

        factory = QueryLoopFactory(
            base_url="http://localhost:11434",
            model="llama3.2",
            config=config,
        )
        llm = factory.create_llm()
        assert llm.circuit_breaker.threshold == 3
        assert llm.circuit_breaker.cooldown_seconds == 120.0


class TestModelRegistryFromConfig:
    def test_from_config_with_models_section(self, tmp_path):
        import yaml
        config_data = {
            "providers": {
                "kimi": {
                    "base_url": "https://api.kimi.com/coding",
                    "adapter": "anthropic",
                },
                "ollama": {
                    "base_url": "http://localhost:11434",
                    "adapter": "openai",
                },
            },
            "models": {
                "kimi-sonnet": {
                    "provider": "kimi",
                    "model_id": "claude-sonnet-4-6",
                    "tags": ["cloud", "paid"],
                },
                "llama3.2": {
                    "provider": "ollama",
                    "model_id": "llama3.2",
                    "tags": ["local", "free"],
                    "is_default": True,
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")

        config = VibeConfig.load(path=config_path, auto_create=False)
        registry = ModelRegistry.from_config(config)

        assert "kimi-sonnet" in registry.list_models()
        assert "llama3.2" in registry.list_models()

        kimi = registry.get("kimi-sonnet")
        assert kimi.provider == "kimi"
        assert kimi.base_url == "https://api.kimi.com/coding"
        assert kimi.model_id == "claude-sonnet-4-6"

        ollama = registry.get("llama3.2")
        assert ollama.is_default is True
        assert ollama.base_url == "http://localhost:11434"

    def test_from_config_fallback_to_builtin(self, tmp_path):
        import yaml
        config_data = {"llm": {"base_url": "http://localhost:11434"}}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")

        config = VibeConfig.load(path=config_path, auto_create=False)
        registry = ModelRegistry.from_config(config)

        # Falls back to built-in profiles
        assert "default" in registry.list_models()

    def test_from_config_invalid_type_raises(self):
        with pytest.raises(TypeError):
            ModelRegistry.from_config("not_a_config")
