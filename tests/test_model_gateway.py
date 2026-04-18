"""Tests for vibe.core.model_gateway."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from vibe.core.model_gateway import LLMClient, LLMResponse, ErrorType


@pytest.fixture
def client():
    return LLMClient(base_url="http://test", model="test-model", api_key="sk-test")


@pytest.mark.asyncio
async def test_complete_success(client):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {"content": "hello", "tool_calls": None},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    mock_resp.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.content == "hello"
    assert result.error is None
    assert result.error_type == ErrorType.NONE
    assert result.usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_complete_rate_limit(client):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limit", request=AsyncMock(), response=mock_resp
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.error_type == ErrorType.RATE_LIMIT_ERROR
    assert result.is_error


@pytest.mark.asyncio
async def test_complete_auth_error(client):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 401
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "unauthorized", request=AsyncMock(), response=mock_resp
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.error_type == ErrorType.AUTHENTICATION_ERROR


@pytest.mark.asyncio
async def test_complete_timeout(client):
    with patch(
        "httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")
    ):
        result = await client.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.error_type == ErrorType.TIMEOUT_ERROR


@pytest.mark.asyncio
async def test_structured_output_valid_json(client):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"answer": 42}'}, "finish_reason": "stop"}],
        "usage": {},
    }
    mock_resp.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        output = await client.structured_output(
            messages=[{"role": "user", "content": "give json"}],
            output_schema={"type": "object", "properties": {"answer": {"type": "integer"}}},
        )

    assert output == {"answer": 42}


@pytest.mark.asyncio
async def test_structured_output_with_markdown_fencing(client):
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "```json\n{\"x\": 1}\n```",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    mock_resp.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        output = await client.structured_output(
            messages=[{"role": "user", "content": "json"}],
            output_schema={"type": "object"},
        )

    assert output == {"x": 1}


def test_headers_include_bearer(client):
    headers = client._get_headers()
    assert headers["Authorization"] == "Bearer sk-test"
