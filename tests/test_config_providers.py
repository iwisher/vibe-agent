"""Tests for VibeConfig provider parsing."""

import os
from pathlib import Path

import pytest
import yaml

from vibe.core.config import VibeConfig, _parse_providers, LLMConfig
from vibe.core.provider_registry import ProviderRegistry


class TestParseProviders:
    def test_backward_compat_no_providers(self):
        """When no providers section exists, synthesize one from llm config."""
        llm = LLMConfig(base_url="http://custom:11434", api_key="sk-test", timeout=90.0)
        reg = _parse_providers({}, llm)
        assert reg.list_providers() == ["default"]
        p = reg.get("default")
        assert p.base_url == "http://custom:11434"
        assert p.adapter_type == "openai"
        assert p.api_key == "sk-test"
        assert p.timeout == 90.0

    def test_parse_multiple_providers(self):
        raw = {
            "ollama": {"base_url": "http://localhost:11434", "adapter": "openai"},
            "kimi": {
                "base_url": "https://api.kimi.com/coding",
                "adapter": "anthropic",
                "api_key_env_var": "KIMI_API_KEY",
                "timeout": 180.0,
            },
        }
        reg = _parse_providers(raw, LLMConfig())
        assert sorted(reg.list_providers()) == ["kimi", "ollama"]
        assert reg.get("kimi").adapter_type == "anthropic"
        assert reg.get("kimi").timeout == 180.0

    def test_parse_provider_missing_base_url_raises(self):
        with pytest.raises(ValueError, match="missing required 'base_url'"):
            _parse_providers({"bad": {}}, LLMConfig())

    def test_parse_provider_invalid_type_raises(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            _parse_providers({"bad": "string"}, LLMConfig())


class TestVibeConfigProviderLoading:
    def test_load_with_providers_section(self, tmp_path: Path):
        config = {
            "providers": {
                "kimi": {
                    "base_url": "https://api.kimi.com/coding",
                    "adapter": "anthropic",
                }
            },
            "llm": {"default_model": "claude-sonnet"},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        assert "kimi" in cfg.providers.list_providers()
        assert cfg.llm.default_model == "claude-sonnet"
        # Backward compat: llm settings still work
        assert cfg.resolve_api_key() is None  # no api_key set

    def test_load_backward_compat_no_providers(self, tmp_path: Path):
        config = {"llm": {"base_url": "http://ollama:11434", "default_model": "mistral"}}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        assert cfg.providers.list_providers() == ["default"]
        assert cfg.providers.get("default").base_url == "http://ollama:11434"

    def test_get_default_provider_with_providers(self, tmp_path: Path):
        config = {
            "providers": {
                "anthropic": {"base_url": "https://api.anthropic.com", "adapter": "anthropic"}
            }
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        dp = cfg.get_default_provider()
        assert dp is not None
        assert dp.name == "anthropic"

    def test_get_default_provider_backward_compat(self, tmp_path: Path):
        config = {"llm": {"base_url": "http://localhost:11434"}}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        dp = cfg.get_default_provider()
        assert dp is not None
        assert dp.base_url == "http://localhost:11434"
        assert dp.name == "default"

    def test_load_preserves_models_section(self, tmp_path: Path):
        config = {
            "providers": {"ollama": {"base_url": "http://localhost:11434"}},
            "models": {
                "default": {"provider": "ollama", "model_id": "llama3.2"},
                "kimi-sonnet": {"provider": "kimi", "model_id": "claude-sonnet-4-6"},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        assert "default" in cfg.models
        assert cfg.models["kimi-sonnet"]["model_id"] == "claude-sonnet-4-6"
