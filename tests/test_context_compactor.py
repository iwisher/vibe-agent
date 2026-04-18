"""Tests for ContextCompactor."""

import pytest

from vibe.core.context_compactor import ContextCompactor, CompactionResult


# ─── compactor_001: Trigger at threshold ───

def test_compactor_001_triggers_at_threshold():
    """compactor_001: When messages exceed token threshold, compaction triggers."""
    compactor = ContextCompactor(max_tokens=4000, chars_per_token=4.0)

    # Build messages that exceed 4000 tokens
    long_content = "word " * 300  # ~305 tokens per message with tiktoken
    messages = []
    for i in range(16):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": long_content})

    estimated = compactor.estimate_tokens(messages)
    assert estimated > 4000, f"Expected >4000 tokens, got {estimated}"
    assert compactor.should_compact(messages) is True

    result = compactor.compact(messages)
    assert isinstance(result, CompactionResult)
    assert result.was_compacted is True
    assert result.strategy_used is not None


def test_compactor_001_does_not_trigger_below_threshold():
    """compactor_001 (inverse): Below threshold, no compaction occurs."""
    compactor = ContextCompactor(max_tokens=4000, chars_per_token=4.0)

    short_messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]

    assert compactor.should_compact(short_messages) is False
    result = compactor.compact(short_messages)
    assert result.was_compacted is False
    assert result.messages == short_messages


# ─── compactor_002: Preserves key info ───

def test_compactor_002_preserves_key_info():
    """compactor_002: Compacted context preserves system msg, tool calls, and recent history."""
    compactor = ContextCompactor(max_tokens=4000, chars_per_token=4.0)

    long_content = "word " * 300  # ~305 tokens per message
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": long_content, "name": "user_1"},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
        # Place tool_calls and tool result in the last 4 messages (kept intact)
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'}, "type": "function"}
            ],
        },
        {"role": "tool", "content": "file contents here", "tool_call_id": "call_1"},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
    ]

    result = compactor.compact(messages)
    assert result.was_compacted is True

    # System message must be preserved
    system_msgs = [m for m in result.messages if m.get("role") == "system"]
    assert len(system_msgs) >= 1
    assert any("helpful assistant" in str(m.get("content", "")) for m in system_msgs)

    # The last 4 non-system messages should be preserved intact
    non_system = [m for m in result.messages if m.get("role") != "system"]
    # summarize_middle strategy: summary + 4 intact = 5 non-system
    assert len(non_system) >= 4

    # Tool call message should survive in the kept portion
    all_tool_calls = []
    for m in result.messages:
        tcs = m.get("tool_calls")
        if tcs:
            all_tool_calls.extend(tcs)
    assert any(tc.get("function", {}).get("name") == "read_file" for tc in all_tool_calls)

    # Tool result should survive
    tool_results = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_results) >= 1
    assert any("file contents" in str(m.get("content", "")) for m in tool_results)


def test_compactor_002_truncate_strategy_preserves_content():
    """compactor_002: When only a few messages exist, truncate strategy shortens but keeps them."""
    compactor = ContextCompactor(max_tokens=4000, chars_per_token=4.0)

    very_long = "x" * 10000  # 10k chars ≈ 2500 tokens each; 2 messages ≈ 5000 tokens > 4000
    messages = [
        {"role": "user", "content": very_long},
        {"role": "assistant", "content": very_long},
        {"role": "user", "content": very_long},
        {"role": "assistant", "content": very_long},
    ]

    result = compactor.compact(messages)
    assert result.was_compacted is True
    assert result.strategy_used == "truncate"

    # All 4 messages should still exist, just truncated
    assert len(result.messages) == 4
    for m in result.messages:
        content = m.get("content", "")
        if isinstance(content, str):
            assert len(content) <= 4000 + 50  # max_chars + truncation suffix overhead
            assert "[Truncated" in content or len(content) < 10000


# ─── Edge case: empty messages ───

def test_compactor_empty_messages():
    """Empty message list should not trigger compaction."""
    compactor = ContextCompactor(max_tokens=4000)
    assert compactor.should_compact([]) is False
    result = compactor.compact([])
    assert result.was_compacted is False
    assert result.messages == []
