"""Tests for Pydantic configuration schema."""

import os
import tempfile

import pytest
from pydantic import ValidationError

from vibe.core.config import (
    EvalConfig,
    ModelConfig,
    PlannerConfig,
    SecurityConfig,
    TraceStoreConfig,
    VibeConfig,
)


class TestModelConfig:
    """Test ModelConfig validation."""

    def test_default_values(self):
        """Should have sensible defaults."""
        config = ModelConfig()
        assert config.provider == "openai"
        assert config.model == "gpt-4o"
        assert config.temperature == 0.7

    def test_temperature_validation(self):
        """Should validate temperature range."""
        with pytest.raises(ValidationError):
            ModelConfig(temperature=3.0)

        with pytest.raises(ValidationError):
            ModelConfig(temperature=-0.1)

        config = ModelConfig(temperature=1.5)
        assert config.temperature == 1.5

    def test_timeout_validation(self):
        """Should validate timeout."""
        with pytest.raises(ValidationError):
            ModelConfig(timeout=0.5)


class TestPlannerConfig:
    """Test PlannerConfig validation."""

    def test_storage_type_validation(self):
        """Should validate storage type."""
        with pytest.raises(ValidationError):
            PlannerConfig(cache_ttl=30)  # Below minimum

        config = PlannerConfig(cache_ttl=120)
        assert config.cache_ttl == 120


class TestTraceStoreConfig:
    """Test TraceStoreConfig validation."""

    def test_storage_type_validation(self):
        """Should validate storage type."""
        with pytest.raises(ValidationError):
            TraceStoreConfig(storage_type="invalid")

        config = TraceStoreConfig(storage_type="json")
        assert config.storage_type == "json"

    def test_retention_validation(self):
        """Should validate retention days."""
        with pytest.raises(ValidationError):
            TraceStoreConfig(retention_days=0)


class TestEvalConfig:
    """Test EvalConfig validation."""

    def test_max_workers_validation(self):
        """Should validate max workers."""
        with pytest.raises(ValidationError):
            EvalConfig(max_workers=0)

        with pytest.raises(ValidationError):
            EvalConfig(max_workers=20)

        config = EvalConfig(max_workers=8)
        assert config.max_workers == 8


class TestSecurityConfig:
    """Test SecurityConfig validation."""

    def test_file_size_validation(self):
        """Should validate max file size."""
        with pytest.raises(ValidationError):
            SecurityConfig(max_file_size_mb=0.01)


class TestVibeConfig:
    """Test root VibeConfig."""

    def test_default_config(self):
        """Should have sensible defaults."""
        config = VibeConfig()
        assert config.debug is False
        assert config.log_level == "INFO"
        assert isinstance(config.model, ModelConfig)
        assert isinstance(config.planner, PlannerConfig)

    def test_log_level_validation(self):
        """Should validate log level."""
        with pytest.raises(ValidationError):
            VibeConfig(log_level="INVALID")

        config = VibeConfig(log_level="DEBUG")
        assert config.log_level == "DEBUG"

    def test_from_dict(self):
        """Should load from dictionary."""
        data = {
            "debug": True,
            "model": {"provider": "anthropic", "model": "claude-3"},
        }
        config = VibeConfig.from_dict(data)
        assert config.debug is True
        assert config.model.provider == "anthropic"

    def test_to_dict(self):
        """Should export to dictionary."""
        config = VibeConfig(debug=True)
        data = config.to_dict()
        assert data["debug"] is True
        assert data["log_level"] == "INFO"

    def test_env_override(self, monkeypatch):
        """Should read environment variables."""
        monkeypatch.setenv("VIBE_DEBUG", "true")
        monkeypatch.setenv("VIBE_LOG_LEVEL", "DEBUG")

        config = VibeConfig()
        assert config.debug is True
        assert config.log_level == "DEBUG"

    def test_from_json_file(self):
        """Should load from JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"debug": true, "log_level": "ERROR"}')
            path = f.name

        try:
            config = VibeConfig.from_file(path)
            assert config.debug is True
            assert config.log_level == "ERROR"
        finally:
            os.unlink(path)

    def test_from_missing_file(self):
        """Should return defaults for missing file."""
        config = VibeConfig.from_file("/nonexistent/config.yaml")
        assert config.debug is False
