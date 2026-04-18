"""Tests for vibe.core.config."""

import os
from pathlib import Path

import pytest
import yaml

from vibe.core.config import (
    VibeConfig,
    LLMConfig,
    FallbackConfig,
    EvalConfig,
    _parse_bool,
    _parse_float,
    _parse_int,
    _parse_list,
)


class TestVibeConfigLoad:
    def test_load_creates_default_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert config_path.exists()
        assert cfg.llm.default_model == "qwen3.5-plus"
        assert cfg.fallback.enabled is True
        assert "qwen3.5-plus" in cfg.fallback.chain

    def test_load_existing_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: minimax-m2.5\n  timeout: 60.0\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        assert cfg.llm.default_model == "minimax-m2.5"
        assert cfg.llm.timeout == 60.0

    def test_env_override_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIBE_MODEL", "kimi-k2.5")
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert cfg.llm.default_model == "kimi-k2.5"

    def test_env_override_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIBE_TIMEOUT", "90")
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert cfg.llm.timeout == 90.0

    def test_env_override_fallback_chain(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIBE_FALLBACK_CHAIN", "a,b,c")
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert cfg.fallback.chain == ["a", "b", "c"]

    def test_malformed_yaml_raises(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("llm: [bad", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            VibeConfig.load(path=config_path, auto_create=False)

    def test_non_dict_yaml_raises(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("just_a_string", encoding="utf-8")
        with pytest.raises(ValueError, match="top-level mapping"):
            VibeConfig.load(path=config_path, auto_create=False)

    def test_scorecard_dir_expands_tilde(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert "~" not in cfg.eval.scorecard_dir
        assert str(tmp_path) in cfg.eval.scorecard_dir

    def test_set_resolved_model(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        cfg.set_resolved_model("minimax-m2.5")
        assert cfg.resolved_model == "minimax-m2.5"


class TestFallbackChain:
    def test_get_fallback_chain_returns_ordered_list(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        chain = cfg.get_fallback_chain()
        assert chain[0] == "qwen3.5-plus"
        assert "kimi-k2.5" in chain

    def test_get_fallback_chain_disabled(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: qwen3.5-plus\nfallback:\n  enabled: false\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        chain = cfg.get_fallback_chain()
        assert chain == ["qwen3.5-plus"]

    def test_default_model_inserted_if_not_in_chain(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: glm-5\nfallback:\n  chain:\n    - minimax-m2.5\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        chain = cfg.get_fallback_chain()
        assert chain[0] == "glm-5"
        assert "minimax-m2.5" in chain


class TestParseHelpers:
    def test_parse_bool_true_values(self):
        assert _parse_bool("true") is True
        assert _parse_bool("TRUE") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True

    def test_parse_bool_false_values(self):
        assert _parse_bool("false") is False
        assert _parse_bool("FALSE") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False
        assert _parse_bool("") is False

    def test_parse_bool_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_bool("maybe")

    def test_parse_float_valid(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "3.14")
        assert _parse_float("TEST_FLOAT", 0.0) == 3.14

    def test_parse_float_missing_uses_fallback(self, monkeypatch):
        monkeypatch.delenv("TEST_FLOAT", raising=False)
        assert _parse_float("TEST_FLOAT", 2.0) == 2.0

    def test_parse_float_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "abc")
        with pytest.raises(ValueError):
            _parse_float("TEST_FLOAT", 0.0)

    def test_parse_int_valid(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "42")
        assert _parse_int("TEST_INT", 0) == 42

    def test_parse_int_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "abc")
        with pytest.raises(ValueError):
            _parse_int("TEST_INT", 0)

    def test_parse_list_env(self, monkeypatch):
        monkeypatch.setenv("TEST_LIST", "a, b, c")
        assert _parse_list(os.getenv("TEST_LIST"), None) == ["a", "b", "c"]

    def test_parse_list_fallback(self):
        assert _parse_list(None, ["x", "y"]) == ["x", "y"]

    def test_parse_list_invalid_fallback_raises(self):
        with pytest.raises(ValueError):
            _parse_list(None, "not_a_list")


class TestResolveApiKey:
    def test_resolve_from_configured_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPLEsay_API_KEY", "sk-test")
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert cfg.resolve_api_key() == "sk-test"

    def test_resolve_fallback_to_llm_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APPLEsay_API_KEY", raising=False)
        monkeypatch.setenv("LLM_API_KEY", "sk-fallback")
        config_path = tmp_path / "config.yaml"
        cfg = VibeConfig.load(path=config_path, auto_create=True)
        assert cfg.resolve_api_key() == "sk-fallback"
