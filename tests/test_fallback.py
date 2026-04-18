"""Tests for LLMClient auto-fallback behavior."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from vibe.core.model_gateway import LLMClient, LLMResponse, ErrorType


class TestLLMClientFallback:
    @pytest.fixture
    def client_with_fallback(self):
        return LLMClient(
            base_url="http://test",
            model="primary-model",
            api_key="sk-test",
            fallback_chain=["fallback-1", "fallback-2"],
            auto_fallback=True,
        )

    @pytest.fixture
    def client_no_fallback(self):
        return LLMClient(
            base_url="http://test",
            model="primary-model",
            api_key="sk-test",
            auto_fallback=False,
        )

    async def test_success_no_fallback_needed(self, client_with_fallback):
        with patch.object(client_with_fallback, "_try_complete") as mock_try:
            mock_try.return_value = LLMResponse(content="hello", model_used="primary-model")
            result = await client_with_fallback.complete([{"role": "user", "content": "hi"}])
            assert result.content == "hello"
            assert result.model_used == "primary-model"
            mock_try.assert_called_once()

    async def test_fallback_on_unavailable_channel(self, client_with_fallback):
        with patch.object(client_with_fallback, "_try_complete") as mock_try:
            # Primary fails with 无可用渠道, fallback-1 succeeds
            primary_error = LLMResponse(
                content="",
                error="当前分组无可用渠道",
                error_type=ErrorType.SERVER_ERROR,
            )
            fallback_success = LLMResponse(content="from fallback")
            mock_try.side_effect = [primary_error, fallback_success]

            result = await client_with_fallback.complete([{"role": "user", "content": "hi"}])
            assert result.content == "from fallback"
            assert result.model_used == "fallback-1"
            assert mock_try.call_count == 2

    async def test_fallback_on_generic_5xx(self, client_with_fallback):
        with patch.object(client_with_fallback, "_try_complete") as mock_try:
            primary_error = LLMResponse(
                content="",
                error="Internal Server Error",
                error_type=ErrorType.SERVER_ERROR,
            )
            fallback_success = LLMResponse(content="fallback ok")
            mock_try.side_effect = [primary_error, fallback_success]

            result = await client_with_fallback.complete([{"role": "user", "content": "hi"}])
            assert result.content == "fallback ok"
            assert result.model_used == "fallback-1"

    async def test_fallback_on_auth_error(self, client_with_fallback):
        """401/403 should trigger fallback (model-specific unavailability)."""
        with patch.object(client_with_fallback, "_try_complete") as mock_try:
            auth_error = LLMResponse(
                content="",
                error="Invalid API key",
                error_type=ErrorType.AUTHENTICATION_ERROR,
            )
            fallback_success = LLMResponse(content="fallback ok")
            mock_try.side_effect = [auth_error, fallback_success]

            result = await client_with_fallback.complete([{"role": "user", "content": "hi"}])
            assert result.content == "fallback ok"
            assert result.model_used == "fallback-1"
            assert mock_try.call_count == 2

    async def test_no_fallback_when_disabled(self, client_no_fallback):
        with patch.object(client_no_fallback, "_try_complete") as mock_try:
            error = LLMResponse(
                content="",
                error="无可用渠道",
                error_type=ErrorType.SERVER_ERROR,
            )
            mock_try.return_value = error

            result = await client_no_fallback.complete([{"role": "user", "content": "hi"}])
            assert result.error_type == ErrorType.SERVER_ERROR
            assert mock_try.call_count == 1

    async def test_all_models_exhausted(self, client_with_fallback):
        with patch.object(client_with_fallback, "_try_complete") as mock_try:
            error = LLMResponse(
                content="",
                error="无可用渠道",
                error_type=ErrorType.SERVER_ERROR,
            )
            mock_try.return_value = error

            result = await client_with_fallback.complete([{"role": "user", "content": "hi"}])
            assert result.error_type == ErrorType.MODEL_UNAVAILABLE
            assert "All models exhausted" in result.error
            assert mock_try.call_count == 3  # primary + 2 fallbacks

    async def test_primary_not_in_fallback_chain(self):
        client = LLMClient(
            base_url="http://test",
            model="primary",
            api_key="sk-test",
            fallback_chain=["primary", "extra"],
            auto_fallback=True,
        )
        with patch.object(client, "_try_complete") as mock_try:
            mock_try.return_value = LLMResponse(content="ok", model_used="primary")
            result = await client.complete([{"role": "user", "content": "hi"}])
            assert result.content == "ok"
            assert mock_try.call_count == 1
