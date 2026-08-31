"""Regression tests for streaming error logging in LLMClient.

Covers three previously silent failure modes:
1. Streams that complete with zero parseable chunks (no log, silent fallback).
2. API errors delivered as SSE data payloads with HTTP 200 (e.g. Gemini).
3. Unexpected exceptions logged without a traceback.
"""

from unittest.mock import MagicMock

import httpx

from vibe.adapters.openai import OpenAIAdapter
from vibe.core.llm_types import ErrorType
from vibe.core.model_gateway import LLMClient


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *args):
        return False


def _ok_chunk(text="hello"):
    return 'data: {"choices": [{"delta": {"content": "%s"}}]}' % text


def _make_client(lines_per_call, logger, adapter=None):
    """Build an LLMClient whose mocked HTTP layer replays SSE lines per call."""
    mock_http = MagicMock(spec=httpx.AsyncClient)
    calls = {"n": 0}

    def _stream(method, url, **kwargs):
        idx = min(calls["n"], len(lines_per_call) - 1)
        calls["n"] += 1
        return _FakeStreamCtx(lines_per_call[idx])

    mock_http.stream = MagicMock(side_effect=_stream)
    return LLMClient(
        base_url="http://test.local",
        model="primary",
        fallback_chain=["backup"],
        auto_fallback=True,
        client=mock_http,
        logger=logger,
        adapter=adapter,
    )


def _warning_messages(logger):
    return [str(call.args[0]) for call in logger.warning.call_args_list]


async def test_empty_stream_is_logged_and_falls_back():
    """A stream ending with no chunks must log a warning and try the next model."""
    logger = MagicMock()
    client = _make_client([["data: [DONE]"], [_ok_chunk()]], logger)

    chunks = [c async for c in client.complete_stream([{"role": "user", "content": "hi"}])]

    assert any(c.content == "hello" for c in chunks)
    warnings = _warning_messages(logger)
    assert any("LLM Stream Empty" in w and "primary" in w for w in warnings)


async def test_empty_stream_yields_error_when_fallback_disabled():
    logger = MagicMock()
    client = _make_client([["data: [DONE]"]], logger)
    client.auto_fallback = False

    chunks = [c async for c in client.complete_stream([{"role": "user", "content": "hi"}])]

    assert len(chunks) == 1
    assert chunks[0].is_error
    assert chunks[0].error_type == ErrorType.SERVER_ERROR
    assert "Empty stream" in chunks[0].error
    warnings = _warning_messages(logger)
    assert any("LLM Stream Empty" in w for w in warnings)


async def test_stream_error_payload_is_logged_and_falls_back():
    """Gemini-style {"error": ...} SSE payloads must surface in the log."""
    logger = MagicMock()
    err_line = (
        'data: {"error": {"code": 400, "message": "API key not valid", '
        '"status": "INVALID_ARGUMENT"}}'
    )
    client = _make_client([[err_line], [_ok_chunk()]], logger)

    chunks = [c async for c in client.complete_stream([{"role": "user", "content": "hi"}])]

    assert any(c.content == "hello" for c in chunks)
    warnings = _warning_messages(logger)
    assert any(
        "LLM Stream Failed" in w and "API key not valid" in w and "HTTP_ERROR" in w
        for w in warnings
    )


async def test_unknown_stream_error_logs_traceback():
    """Unexpected adapter bugs must include a traceback in the session log."""

    class _BrokenAdapter(OpenAIAdapter):
        def parse_stream_chunk(self, chunk_json):
            raise AttributeError("'str' object has no attribute 'get'")

    logger = MagicMock()
    client = _make_client([[_ok_chunk()]], logger, adapter=_BrokenAdapter())

    chunks = [c async for c in client.complete_stream([{"role": "user", "content": "hi"}])]

    assert len(chunks) == 1
    assert chunks[0].is_error
    assert chunks[0].error_type == ErrorType.UNKNOWN_ERROR
    warnings = _warning_messages(logger)
    assert any(
        "LLM Stream Failed" in w and "Traceback" in w and "AttributeError" in w for w in warnings
    )


async def test_complete_accepts_bare_prompt_string():
    """Memory/planner callers pass a raw prompt string; it must be normalized
    into a single user message instead of reaching the adapter as a string."""
    from unittest.mock import AsyncMock

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_post_res = MagicMock(spec=httpx.Response)
    mock_post_res.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    mock_post_res.raise_for_status.return_value = None
    mock_http.post = AsyncMock(return_value=mock_post_res)

    client = LLMClient(
        base_url="http://test.local",
        model="primary",
        client=mock_http,
    )

    response = await client.complete("summarize this session")

    assert response.content == "ok"
    payload = mock_http.post.call_args[1]["json"]
    assert payload["messages"] == [{"role": "user", "content": "summarize this session"}]


async def test_complete_from_foreign_event_loop_uses_temporary_client(monkeypatch):
    """The pooled httpx client binds to the loop of first use. Callers on a
    different loop (e.g. SmartApprover via asyncio.run on a worker thread)
    must get a temporary client instead of crashing with
    "bound to a different event loop"."""
    import asyncio

    from vibe.core import model_gateway

    created = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            created.append(self)

        async def post(self, url, json=None, headers=None):
            resp = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            }
            resp.raise_for_status.return_value = None
            return resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def aclose(self):
            return None

    monkeypatch.setattr(model_gateway.httpx, "AsyncClient", _FakeAsyncClient)

    client = LLMClient(base_url="http://test.local", model="primary")

    # First call binds the pooled client to this loop
    res = await client.complete([{"role": "user", "content": "hi"}])
    assert res.content == "ok"
    assert len(created) == 1

    # Call from a different loop on a worker thread
    def _run_other_loop():
        return asyncio.run(client.complete([{"role": "user", "content": "hi"}]))

    res2 = await asyncio.to_thread(_run_other_loop)
    assert res2.content == "ok"
    assert len(created) == 2  # temporary client used for the foreign loop

    # Back on the original loop, the pooled client is reused
    res3 = await client.complete([{"role": "user", "content": "hi"}])
    assert res3.content == "ok"
    assert len(created) == 2


async def test_circuit_breaker_open_explains_skip():
    """When the circuit breaker skips every model, the error must say so
    instead of the misleading 'No models available in fallback chain'."""
    logger = MagicMock()
    mock_http = MagicMock(spec=httpx.AsyncClient)
    client = LLMClient(
        base_url="http://test.local",
        model="primary",
        client=mock_http,
        logger=logger,
    )
    for _ in range(client.circuit_breaker.threshold):
        client.circuit_breaker.record_failure("primary")

    chunks = [c async for c in client.complete_stream([{"role": "user", "content": "hi"}])]

    assert len(chunks) == 1
    assert chunks[0].is_error
    assert "circuit breaker" in chunks[0].error
    mock_http.stream.assert_not_called()
    warnings = _warning_messages(logger)
    assert any("LLM Stream Skipped" in w and "primary" in w for w in warnings)
