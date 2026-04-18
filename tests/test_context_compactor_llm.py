"""Tests for ContextCompactor LLM summarization strategy."""

import pytest

from vibe.core.context_compactor import (
    CompactionResult,
    ContextCompactor,
    SummarizationStrategy,
)


async def fake_summarizer(messages):
    """Fake LLM summarizer for testing."""
    return f"Summary of {len(messages)} messages"


async def failing_summarizer(messages):
    raise RuntimeError("LLM down")


def _make_messages(count: int):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 500}
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_compactor_llm_summarize_success():
    compactor = ContextCompactor(
        max_tokens=100,
        chars_per_token=1.0,
        strategy=SummarizationStrategy.LLM_SUMMARIZE,
        summarize_fn=fake_summarizer,
        preserve_recent=2,
    )
    messages = _make_messages(6)
    result = await compactor.compact_async(messages)
    assert result.was_compacted is True
    assert result.strategy_used == "llm_summarize"
    assert result.summary_text == "Summary of 4 messages"
    assert len(result.messages) == 3  # summary + 2 preserved
    assert result.messages[0]["role"] == "system"
    assert "Earlier conversation summary" in result.messages[0]["content"]


@pytest.mark.asyncio
async def test_compactor_llm_summarize_fallback_on_failure():
    compactor = ContextCompactor(
        max_tokens=100,
        chars_per_token=1.0,
        strategy=SummarizationStrategy.LLM_SUMMARIZE,
        summarize_fn=failing_summarizer,
        preserve_recent=2,
    )
    messages = _make_messages(6)
    result = await compactor.compact_async(messages)
    assert result.was_compacted is True
    assert result.strategy_used == "summarize_middle"  # fallback
    assert result.summary_text is None


@pytest.mark.asyncio
async def test_compactor_llm_summarize_no_fn_uses_fallback():
    compactor = ContextCompactor(
        max_tokens=100,
        chars_per_token=1.0,
        strategy=SummarizationStrategy.LLM_SUMMARIZE,
        summarize_fn=None,
        preserve_recent=2,
    )
    messages = _make_messages(6)
    result = await compactor.compact_async(messages)
    assert result.was_compacted is True
    assert result.strategy_used == "summarize_middle"


@pytest.mark.asyncio
async def test_compactor_truncate_strategy_ignores_summarize_fn():
    compactor = ContextCompactor(
        max_tokens=100,
        chars_per_token=1.0,
        strategy=SummarizationStrategy.TRUNCATE,
        summarize_fn=fake_summarizer,
        preserve_recent=2,
    )
    messages = _make_messages(6)
    result = await compactor.compact_async(messages)
    assert result.was_compacted is True
    assert result.strategy_used == "summarize_middle"  # TRUNCATE uses old behavior
    assert result.summary_text is None


@pytest.mark.asyncio
async def test_compactor_preserve_recent_respected():
    compactor = ContextCompactor(
        max_tokens=50,
        chars_per_token=1.0,
        strategy=SummarizationStrategy.LLM_SUMMARIZE,
        summarize_fn=fake_summarizer,
        preserve_recent=3,
    )
    messages = _make_messages(8)
    result = await compactor.compact_async(messages)
    assert result.was_compacted is True
    assert len(result.messages) == 4  # summary + 3 preserved
