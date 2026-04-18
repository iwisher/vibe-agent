"""Tests for vibe.core.health_check."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from vibe.core.health_check import ModelHealthChecker, is_unavailable_error
from vibe.core.config import VibeConfig


class TestIsUnavailableError:
    def test_applesay_chinese_message(self):
        assert is_unavailable_error("当前分组无可用渠道") is True

    def test_english_message(self):
        assert is_unavailable_error("no available channel for model") is True

    def test_empty_string(self):
        assert is_unavailable_error("") is False

    def test_other_error(self):
        assert is_unavailable_error("rate limit exceeded") is False


class TestCheckAvailable:
    async def test_available_model_returns_true(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch.object(checker, "_get_headers", return_value={"Authorization": "Bearer sk-test"}):
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_response = AsyncMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"choices": [{"message": {"content": ""}}]}
                mock_post.return_value = mock_response

                result = await checker.check_available("MiniMax-M2.5")
                assert result is True

    async def test_unavailable_channel_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 400
            mock_response.text = '{"error": {"message": "\u5f53\u524d\u5206\u7ec4\u65e0\u53ef\u7528\u6e20\u9053"}}'
            mock_post.return_value = mock_response

            result = await checker.check_available("kimi-k2.5")
            assert result is False

    async def test_5xx_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"
            mock_post.return_value = mock_response

            result = await checker.check_available("qwen3.5-plus")
            assert result is False

    async def test_timeout_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("timeout")):
            result = await checker.check_available("glm-5")
            assert result is False

    async def test_network_error_returns_false(self):
        checker = ModelHealthChecker(api_key="sk-test")
        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("connection failed")):
            result = await checker.check_available("minimax-m2.7")
            assert result is False


class TestResolveModel:
    async def test_first_available_model_returned(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: qwen3.5-plus\n"
            "fallback:\n  chain:\n    - qwen3.5-plus\n    - minimax-m2.5\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available") as mock_check:
            # qwen3.5-plus unavailable, minimax-m2.5 available
            mock_check.side_effect = [False, True]
            result = await checker.resolve_model(cfg)
            assert result == "minimax-m2.5"
            assert cfg.resolved_model == "minimax-m2.5"

    async def test_default_model_if_all_available(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: minimax-m2.5\nfallback:\n  chain:\n    - minimax-m2.5\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available", return_value=True):
            result = await checker.resolve_model(cfg)
            assert result == "minimax-m2.5"

    async def test_returns_default_when_none_available(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: qwen3.5-plus\n"
            "fallback:\n  chain:\n    - qwen3.5-plus\n    - minimax-m2.5\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available", return_value=False):
            result = await checker.resolve_model(cfg)
            # Falls back to default as last resort
            assert result == "qwen3.5-plus"


class TestResolveWithRetry:
    async def test_retry_eventually_succeeds(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llm:\n  default_model: qwen3.5-plus\n"
            "fallback:\n  chain:\n    - minimax-m2.5\n  max_retries: 2\n",
            encoding="utf-8",
        )
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        checker = ModelHealthChecker(api_key="sk-test")

        with patch.object(checker, "check_available") as mock_check:
            # Attempt 1: primary fails, fallback fails, default fails
            # Attempt 2: primary fails, fallback succeeds
            mock_check.side_effect = [False, False, False, False, True]
            result = await checker.resolve_with_retry(cfg)
            assert result == "minimax-m2.5"
