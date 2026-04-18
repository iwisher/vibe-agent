"""Tests for vibe.core.context_compactor."""

import pytest

from vibe.core.context_compactor import ContextCompactor


def test_estimate_tokens_basic():
    compactor = ContextCompactor(max_tokens=100)
    messages = [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "hi"},
    ]
    tokens = compactor.estimate_tokens(messages)
    assert isinstance(tokens, int)
    assert tokens > 0


def test_estimate_tokens_uses_tiktoken_when_available():
    from vibe.core.context_compactor import _get_encoding
    compactor = ContextCompactor(max_tokens=100)
    messages = [{"role": "user", "content": "hello world"}]
    tokens = compactor.estimate_tokens(messages)
    enc = _get_encoding()
    if enc is not None:
        # tiktoken should give a more precise count than naive fallback
        assert tokens >= len(enc.encode("hello world")) + 4  # + message overhead
    else:
        # fallback path
        assert tokens == int(len("hello world") / 4.0)


def test_should_compact_true():
    compactor = ContextCompactor(max_tokens=5)
    messages = [{"role": "user", "content": "a" * 2000}]
    assert compactor.should_compact(messages)


def test_compact_noop_when_under_limit():
    compactor = ContextCompactor(max_tokens=10000)
    messages = [{"role": "user", "content": "short"}]
    result = compactor.compact(messages)
    assert not result.was_compacted
    assert result.messages == messages


def test_compact_truncate_when_few_messages():
    compactor = ContextCompactor(max_tokens=5)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a" * 5000},
    ]
    result = compactor.compact(messages)
    assert result.was_compacted
    assert result.strategy_used == "truncate"
    assert "[Truncated" in result.messages[1]["content"]


def test_compact_summarize_middle():
    compactor = ContextCompactor(max_tokens=5)
    messages = [{"role": "user", "content": "a" * 500} for _ in range(10)]
    result = compactor.compact(messages)
    assert result.was_compacted
    assert result.strategy_used == "summarize_middle"
    assert any(m.get("role") == "system" and "summarized" in m["content"] for m in result.messages)
    # Should keep last 4 messages intact
    assert len(result.messages) == 5  # summary + 4 kept intact
