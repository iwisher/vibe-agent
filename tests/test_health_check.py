"""Tests for vibe.core.health_check."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from vibe.core.health_check import ModelHealthChecker
from vibe.core.config import VibeConfig


class TestCheckAvailable:
    async def test_available_model_returns_true(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch.object(checker, "_get_headers", return_value={"Authorization": "Bearer sk-test"}):
            with patch("httpx.AsyncClient.get") as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"data": [{"id": "default"}]}
                mock_get.return_value = mock_response

                result = await checker.check_available("default")
                assert result is True

    async def test_4xx_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = '{"error": {"message": "bad request"}}'
            mock_get.return_value = mock_response

            result = await checker.check_available("default")
            assert result is False

    async def test_5xx_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"
            mock_get.return_value = mock_response

            result = await checker.check_available("default")
            assert result is False

    async def test_timeout_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("timeout")):
            result = await checker.check_available("default")
            assert result is False

    async def test_network_error_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("connection failed")):
            result = await checker.check_available("default")
            assert result is False


class TestResolveModel:
    async def test_first_available_model_returned(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: model-a\n"
            "fallback:\n  chain:\n    - model-a\n    - model-b\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available") as mock_check:
            # model-a unavailable, model-b available
            mock_check.side_effect = [False, True]
            result = await checker.resolve_model(cfg)
            assert result == "model-b"
            assert cfg.resolved_model == "model-b"

    async def test_default_model_if_all_available(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: model-b\nfallback:\n  chain:\n    - model-b\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available", return_value=True):
            result = await checker.resolve_model(cfg)
            assert result == "model-b"

    async def test_returns_default_when_none_available(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: model-a\n"
            "fallback:\n  chain:\n    - model-a\n    - model-b\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available", return_value=False):
            result = await checker.resolve_model(cfg)
            # Falls back to default as last resort
            assert result == "model-a"


class TestResolveWithRetry:
    async def test_retry_eventually_succeeds(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: model-a\n"
            "fallback:\n  chain:\n    - model-b\n  max_retries: 2\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available") as mock_check:
            # Attempt 1: primary fails, fallback fails, default fails
            # Attempt 2: primary fails, fallback succeeds
            mock_check.side_effect = [False, False, False, False, True]
            result = await checker.resolve_with_retry(cfg)
            assert result == "model-b"
