"""Tests for LLMClient streaming gateway."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from vibe.core.llm_types import ErrorType
from vibe.core.model_gateway import LLMClient, StreamExecutionError


class MockStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def base_client():
    return LLMClient(
        base_url="http://test",
        model="model-1",
        api_key="sk-test",
        auto_fallback=True,
        fallback_chain=["model-2"],
    )


@pytest.mark.asyncio
async def test_stream_success(base_client):
    async def mock_aiter_lines():
        yield 'data: {"choices": [{"delta": {"content": "hello "}}]}'
        yield ""
        yield 'data: {"choices": [{"delta": {"content": "world"}}]}'
        yield 'data: {"choices": [{"delta": {"content": "!"}}]}'
        yield "data: [DONE]"

    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = mock_aiter_lines

    with patch(
        "httpx.AsyncClient.stream", return_value=MockStreamContext(mock_resp)
    ):
        chunks = []
        async for chunk in base_client.complete_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].content == "hello "
    assert chunks[1].content == "world"
    assert chunks[2].content == "!"
    assert all(c.model_used == "model-1" for c in chunks)
    assert not base_client.circuit_breaker.is_open("model-1")


@pytest.mark.asyncio
async def test_stream_with_reasoning(base_client):
    async def mock_aiter_lines():
        yield 'data: {"choices": [{"delta": {"reasoning_content": "thinking "}}]}'
        yield (
            'data: {"choices": [{"delta": {"reasoning_content": "hard... ", '
            '"content": "hello"}}]}'
        )
        yield "data: [DONE]"

    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = mock_aiter_lines

    with patch("httpx.AsyncClient.stream", return_value=MockStreamContext(mock_resp)):
        chunks = []
        async for chunk in base_client.complete_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0].reasoning_content == "thinking "
    assert chunks[0].content == ""
    assert chunks[1].reasoning_content == "hard... "
    assert chunks[1].content == "hello"

    # Verify we can aggregate them
    full_content = "".join(c.content for c in chunks)
    full_reasoning = "".join(c.reasoning_content for c in chunks)
    assert full_content == "hello"
    assert full_reasoning == "thinking hard... "


@pytest.mark.asyncio
async def test_stream_pre_stream_fallback(base_client):
    async def mock_aiter_lines_model2():
        yield 'data: {"choices": [{"delta": {"content": "hello from model 2"}}]}'
        yield "data: [DONE]"

    # Setup mock response for successful model 2
    mock_resp_model2 = AsyncMock(spec=httpx.Response)
    mock_resp_model2.status_code = 200
    mock_resp_model2.raise_for_status = MagicMock()
    mock_resp_model2.aiter_lines = mock_aiter_lines_model2

    # Side effect: first call (model-1) raises connection error, second (model-2) succeeds
    call_count = 0

    def stream_side_effect(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        return MockStreamContext(mock_resp_model2)

    with patch("httpx.AsyncClient.stream", side_effect=stream_side_effect):
        chunks = []
        async for chunk in base_client.complete_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].content == "hello from model 2"
    assert chunks[0].model_used == "model-2"

    # Assert circuit breaker updated correctly
    assert base_client.circuit_breaker._state("model-1").consecutive_failures == 1
    assert base_client.circuit_breaker._state("model-2").consecutive_failures == 0


@pytest.mark.asyncio
async def test_stream_mid_stream_failure(base_client):
    async def mock_aiter_lines():
        yield 'data: {"choices": [{"delta": {"content": "hello "}}]}'
        raise httpx.RemoteProtocolError("Connection closed unexpectedly")

    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = mock_aiter_lines

    with patch("httpx.AsyncClient.stream", return_value=MockStreamContext(mock_resp)):
        generator = base_client.complete_stream(messages=[{"role": "user", "content": "hi"}])

        # We should be able to get the first chunk successfully
        chunk1 = await anext(generator)
        assert chunk1.content == "hello "

        # The next iteration must fail loud with StreamExecutionError
        with pytest.raises(StreamExecutionError) as exc_info:
            await anext(generator)

        assert "Mid-stream failure for model-1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_stream_all_models_fail(base_client):
    def stream_side_effect(method, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch("httpx.AsyncClient.stream", side_effect=stream_side_effect):
        chunks = []
        async for chunk in base_client.complete_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].is_error
    assert "All models exhausted" in chunks[0].error
    assert chunks[0].error_type == ErrorType.MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_stream_circuit_breaker_open(base_client):
    # Set model-1 circuit breaker to open
    base_client.circuit_breaker.record_failure("model-1")
    base_client.circuit_breaker.record_failure("model-1")
    base_client.circuit_breaker.record_failure("model-1")
    base_client.circuit_breaker.record_failure("model-1")
    base_client.circuit_breaker.record_failure("model-1")
    assert base_client.circuit_breaker.is_open("model-1")

    async def mock_aiter_lines_model2():
        yield 'data: {"choices": [{"delta": {"content": "hello from model 2"}}]}'
        yield "data: [DONE]"

    mock_resp_model2 = AsyncMock(spec=httpx.Response)
    mock_resp_model2.status_code = 200
    mock_resp_model2.raise_for_status = MagicMock()
    mock_resp_model2.aiter_lines = mock_aiter_lines_model2

    with patch(
        "httpx.AsyncClient.stream", return_value=MockStreamContext(mock_resp_model2)
    ) as mock_stream:
        chunks = []
        async for chunk in base_client.complete_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].content == "hello from model 2"
    assert chunks[0].model_used == "model-2"

    # Assert model-1 was skipped and not even called
    for call in mock_stream.call_args_list:
        # Check payload
        payload = call.kwargs.get("json", {})
        assert payload.get("model") == "model-2"
