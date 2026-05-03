"""Tests for ProviderRegistry and ProviderProfile."""

import os
from unittest.mock import patch

import pytest

from vibe.adapters.anthropic import AnthropicAdapter
from vibe.adapters.openai import OpenAIAdapter
from vibe.core.model_gateway import LLMClient
from vibe.core.provider_registry import ProviderProfile, ProviderRegistry


class TestProviderProfile:
    def test_resolve_api_key_explicit(self):
        p = ProviderProfile(name="test", base_url="http://localhost", api_key="sk-explicit")
        assert p.resolve_api_key() == "sk-explicit"

    def test_resolve_api_key_from_env(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "sk-env"}, clear=False):
            p = ProviderProfile(
                name="test", base_url="http://localhost", api_key_env_var="TEST_API_KEY"
            )
            assert p.resolve_api_key() == "sk-env"

    def test_resolve_api_key_none(self):
        p = ProviderProfile(name="test", base_url="http://localhost", api_key_env_var=None)
        assert p.resolve_api_key() is None

    def test_create_adapter_openai(self):
        p = ProviderProfile(name="test", base_url="http://localhost", adapter_type="openai")
        adapter = p.create_adapter()
        assert isinstance(adapter, OpenAIAdapter)

    def test_create_adapter_anthropic(self):
        p = ProviderProfile(
            name="test", base_url="https://api.anthropic.com", adapter_type="anthropic"
        )
        adapter = p.create_adapter()
        assert isinstance(adapter, AnthropicAdapter)

    def test_create_adapter_unknown_raises(self):
        p = ProviderProfile(name="test", base_url="http://localhost", adapter_type="unknown")
        with pytest.raises(KeyError, match="Unknown adapter"):
            p.create_adapter()

    def test_create_client(self):
        p = ProviderProfile(
            name="test",
            base_url="http://localhost:11434",
            adapter_type="openai",
            default_model="llama3.2",
            timeout=30.0,
        )
        client = p.create_client()
        assert isinstance(client, LLMClient)
        assert client.base_url == "http://localhost:11434"
        assert client.model == "llama3.2"

    def test_create_client_with_override_model(self):
        p = ProviderProfile(name="test", base_url="http://localhost", default_model="default")
        client = p.create_client(model_id="mistral")
        assert client.model == "mistral"


class TestProviderRegistry:
    def test_register_and_get(self):
        reg = ProviderRegistry()
        p = ProviderProfile(name="kimi", base_url="https://api.kimi.com/coding")
        reg.register(p)
        assert reg.get("kimi") == p
        assert reg.get("missing") is None

    def test_remove(self):
        reg = ProviderRegistry()
        reg.register(ProviderProfile(name="kimi", base_url="https://api.kimi.com"))
        reg.remove("kimi")
        assert reg.get("kimi") is None

    def test_list_providers(self):
        reg = ProviderRegistry()
        reg.register(ProviderProfile(name="a", base_url="http://a"))
        reg.register(ProviderProfile(name="b", base_url="http://b"))
        assert reg.list_providers() == ["a", "b"]

    def test_resolve_client_success(self):
        reg = ProviderRegistry()
        reg.register(ProviderProfile(name="ollama", base_url="http://localhost:11434"))
        client = reg.resolve_client("ollama", model_id="llama3.2")
        assert isinstance(client, LLMClient)
        assert client.model == "llama3.2"

    def test_resolve_client_unknown_raises(self):
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="Unknown provider"):
            reg.resolve_client("missing")

    def test_from_dict(self):
        data = {
            "ollama": {
                "base_url": "http://localhost:11434",
                "adapter_type": "openai",
                "timeout": 60.0,
            },
            "kimi": {
                "base_url": "https://api.kimi.com/coding",
                "adapter_type": "anthropic",
                "api_key_env_var": "KIMI_API_KEY",
            },
        }
        reg = ProviderRegistry.from_dict(data)
        assert reg.list_providers() == ["ollama", "kimi"]
        ollama = reg.get("ollama")
        assert ollama.base_url == "http://localhost:11434"
        assert ollama.adapter_type == "openai"
        assert ollama.timeout == 60.0
        kimi = reg.get("kimi")
        assert kimi.adapter_type == "anthropic"
        assert kimi.api_key_env_var == "KIMI_API_KEY"

    def test_to_dict(self):
        reg = ProviderRegistry()
        reg.register(ProviderProfile(name="p1", base_url="http://p1"))
        d = reg.to_dict()
        assert "p1" in d
        assert d["p1"]["base_url"] == "http://p1"

    def test_default_ollama_factory(self):
        with patch.dict(os.environ, {"VIBE_BASE_URL": "http://ollama.local:11434"}, clear=False):
            reg = ProviderRegistry.default_ollama()
            assert reg.list_providers() == ["ollama"]
            assert reg.get("ollama").base_url == "http://ollama.local:11434"
