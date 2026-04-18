"""Context compaction for managing token limits."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _get_encoding():
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


@dataclass
class CompactionResult:
    messages: List[Dict[str, Any]]
    was_compacted: bool = False
    strategy_used: Optional[str] = None


class ContextCompactor:
    def __init__(self, max_tokens: int = 8000, chars_per_token: float = 4.0):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self._encoding = _get_encoding()

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        if self._encoding is not None:
            total = 0
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += len(self._encoding.encode(content))
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    total += len(self._encoding.encode(str(tool_calls)))
                # Add overhead per message (OpenAI style ~3-4 tokens)
                total += 4
            return total

        # Fallback to naive estimation
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                total_chars += len(str(tool_calls))
        return int(total_chars / self.chars_per_token)

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        return self.estimate_tokens(messages) > self.max_tokens

    def compact(self, messages: List[Dict[str, Any]]) -> CompactionResult:
        if not self.should_compact(messages):
            return CompactionResult(messages=messages)

        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= 4:
            compacted = system_messages + [self._truncate(m) for m in non_system]
            return CompactionResult(messages=compacted, was_compacted=True, strategy_used="truncate")

        to_summarize = non_system[:-4]
        keep_intact = non_system[-4:]
        summary = {
            "role": "system",
            "content": f"[Context summarized: {len(to_summarize)} earlier messages omitted]",
        }
        compacted = system_messages + [summary] + keep_intact
        return CompactionResult(
            messages=compacted,
            was_compacted=True,
            strategy_used="summarize_middle",
        )

    def _truncate(self, message: Dict[str, Any], max_chars: int = 4000) -> Dict[str, Any]:
        content = message.get("content", "")
        if isinstance(content, str) and len(content) > max_chars:
            truncated = content[:max_chars] + f"\n\n[Truncated from {len(content)} chars]"
            return {**message, "content": truncated}
        return message
