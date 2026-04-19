"""Tests for provider-aware health checking (Phase C)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from vibe.core.config import VibeConfig
from vibe.core.health_check import ModelHealthChecker
from vibe.core.provider_registry import ProviderProfile, ProviderRegistry
from vibe.evals.model_registry import ModelProfile, ModelRegistry


class TestProviderAwareCheck:
    async def test_check_with_openai_provider(self):
        registry = ProviderRegistry()
        registry.register(
            ProviderProfile(name="ollama", base_url="http://localhost:11434", adapter_type="openai")
        )
        checker = ModelHealthChecker(provider_registry=registry)

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": [{"id": "llama3.2"}]}
            mock_get.return_value = mock_response

            result = await checker.check_available("llama3.2", provider_name="ollama")
            assert result is True

    async def test_check_with_anthropic_provider(self):
        registry = ProviderRegistry()
        registry.register(
            ProviderProfile(
                name="kimi",
                base_url="https://api.kimi.com/coding",
                adapter_type="anthropic",
                api_key="sk-test",
            )
        )
        checker = ModelHealthChecker(provider_registry=registry)

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": [{"id": "claude-sonnet"}]}
            mock_get.return_value = mock_response

            result = await checker.check_available("claude-sonnet", provider_name="kimi")
            assert result is True
            # Verify x-api-key header was used (not Bearer)
            call_kwargs = mock_get.call_args.kwargs
            assert call_kwargs["headers"]["x-api-key"] == "sk-test"
            assert "Authorization" not in call_kwargs["headers"]

    async def test_check_unknown_provider_returns_false(self):
        checker = ModelHealthChecker(provider_registry=ProviderRegistry())
        result = await checker.check_available("model", provider_name="missing")
        assert result is False

    async def test_check_provider_4xx_returns_false(self):
        registry = ProviderRegistry()
        registry.register(ProviderProfile(name="p", base_url="http://localhost", adapter_type="openai"))
        checker = ModelHealthChecker(provider_registry=registry)

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_get.return_value = mock_response
            result = await checker.check_available("model", provider_name="p")
            assert result is False

    async def test_check_provider_timeout_returns_false(self):
        registry = ProviderRegistry()
        registry.register(ProviderProfile(name="p", base_url="http://localhost", adapter_type="openai"))
        checker = ModelHealthChecker(provider_registry=registry)

        with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("timeout")):
            result = await checker.check_available("model", provider_name="p")
            assert result is False

    async def test_check_fallback_to_post_probe(self):
        """When GET /v1/models returns empty list, POST probe should be tried."""
        registry = ProviderRegistry()
        registry.register(ProviderProfile(name="p", base_url="http://localhost", adapter_type="openai"))
        checker = ModelHealthChecker(provider_registry=registry)

        get_mock = MagicMock()
        get_mock.status_code = 200
        get_mock.json.return_value = {"data": []}  # Empty models list

        post_mock = MagicMock()
        post_mock.status_code = 200
        post_mock.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

        with patch("httpx.AsyncClient.get", return_value=get_mock):
            with patch("httpx.AsyncClient.post", return_value=post_mock):
                result = await checker.check_available("model", provider_name="p")
                assert result is True


class TestResolveModelWithProviders:
    async def test_resolve_uses_provider_from_model_profile(self):
        """When ModelProfile specifies a provider, health check uses that provider's adapter."""
        provider_reg = ProviderRegistry()
        provider_reg.register(
            ProviderProfile(name="kimi", base_url="https://api.kimi.com/coding", adapter_type="anthropic")
        )
        checker = ModelHealthChecker(provider_registry=provider_reg)

        model_reg = ModelRegistry()
        model_reg.add_profile(
            ModelProfile(
                name="kimi-sonnet",
                provider="kimi",
                base_url="https://api.kimi.com/coding",
                model_id="claude-sonnet-4-6",
            )
        )

        config = VibeConfig.load(auto_create=False)
        config.llm.default_model = "default"
        config.fallback.chain = ["kimi-sonnet"]

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": [{"id": "claude-sonnet-4-6"}]}
            mock_get.return_value = mock_response

            resolved = await checker.resolve_model(config, registry=model_reg)
            assert resolved == "kimi-sonnet"

    async def test_resolve_fallback_cross_provider(self):
        """Fallback chain can cross providers (e.g., kimi fails, ollama succeeds)."""
        provider_reg = ProviderRegistry()
        provider_reg.register(
            ProviderProfile(name="kimi", base_url="https://api.kimi.com/coding", adapter_type="anthropic")
        )
        provider_reg.register(
            ProviderProfile(name="ollama", base_url="http://localhost:11434", adapter_type="openai")
        )
        checker = ModelHealthChecker(provider_registry=provider_reg)

        model_reg = ModelRegistry()
        model_reg.add_profile(
            ModelProfile(name="kimi-sonnet", provider="kimi", base_url="https://api.kimi.com/coding", model_id="claude-sonnet")
        )
        model_reg.add_profile(
            ModelProfile(name="llama3.2", provider="ollama", base_url="http://localhost:11434", model_id="llama3.2")
        )

        config = VibeConfig.load(auto_create=False)
        config.llm.default_model = "kimi-sonnet"
        config.fallback.chain = ["kimi-sonnet", "llama3.2"]

        call_count = 0
        async def _mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            if call_count == 1:
                # First call (kimi) fails
                mock_response.status_code = 503
            else:
                # Second call (ollama) succeeds
                mock_response.status_code = 200
                mock_response.json.return_value = {"data": [{"id": "llama3.2"}]}
            return mock_response

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=_mock_get):
            resolved = await checker.resolve_model(config, registry=model_reg)
            assert resolved == "llama3.2"
            assert call_count == 2
