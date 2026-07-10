"""Tests for streaming capabilities of OpenAI and Anthropic adapters."""

from vibe.adapters.anthropic import AnthropicAdapter
from vibe.adapters.openai import OpenAIAdapter


def test_openai_stream_text_chunks():
    adapter = OpenAIAdapter()
    chunk = {"choices": [{"delta": {"content": "hello world"}, "finish_reason": None}]}
    res = adapter.parse_stream_chunk(chunk)
    assert res is not None
    assert res.content == "hello world"
    assert res.reasoning_content == ""


def test_openai_stream_reasoning_chunks():
    adapter = OpenAIAdapter()
    chunk = {
        "choices": [
            {"delta": {"reasoning_content": "let's think about this..."}, "finish_reason": None}
        ]
    }
    res = adapter.parse_stream_chunk(chunk)
    assert res is not None
    assert res.content == ""
    assert res.reasoning_content == "let's think about this..."


def test_openai_stream_done_sentinel():
    adapter = OpenAIAdapter()
    assert adapter.parse_stream_chunk("[DONE]") is None
    assert adapter.parse_stream_chunk("") is None
    assert adapter.parse_stream_chunk({}) is None
    assert adapter.parse_stream_chunk({"done": True}) is None


def test_anthropic_stream_text_chunks():
    adapter = AnthropicAdapter()
    chunk = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "hello anthropic"},
    }
    res = adapter.parse_stream_chunk(chunk)
    assert res is not None
    assert res.content == "hello anthropic"
    assert res.reasoning_content == ""


def test_anthropic_stream_thinking_chunks():
    adapter = AnthropicAdapter()
    chunk = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "thinking hard"},
    }
    res = adapter.parse_stream_chunk(chunk)
    assert res is not None
    assert res.content == ""
    assert res.reasoning_content == "thinking hard"


def test_anthropic_stream_done_sentinel():
    adapter = AnthropicAdapter()
    assert adapter.parse_stream_chunk("[DONE]") is None
    assert adapter.parse_stream_chunk("") is None
    assert adapter.parse_stream_chunk({}) is None
    assert adapter.parse_stream_chunk({"type": "ping"}) is None
