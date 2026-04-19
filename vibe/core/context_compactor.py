"""Context compaction for managing token limits."""

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


class SummarizationStrategy(Enum):
    """Strategy for reducing context when token budget is exceeded."""
    TRUNCATE = auto()       # Keep N recent messages, drop the rest
    LLM_SUMMARIZE = auto()  # Use LLM to generate a semantic summary


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
    summary_text: Optional[str] = None


class ContextCompactor:
    def __init__(
        self,
        max_tokens: int = 8000,
        chars_per_token: float = 4.0,
        strategy: SummarizationStrategy = SummarizationStrategy.TRUNCATE,
        summarize_fn: Optional[Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, str]]] = None,
        preserve_recent: int = 4,
        max_chars_per_msg: int = 4000,
        config: Optional[Any] = None,
    ):
        if config is not None:
            # Support passing a CompactorConfig or VibeConfig directly
            cfg = getattr(config, "compactor", config)
            max_tokens = getattr(cfg, "max_tokens", max_tokens)
            chars_per_token = getattr(cfg, "chars_per_token", chars_per_token)
            preserve_recent = getattr(cfg, "preserve_recent", preserve_recent)
            max_chars_per_msg = getattr(cfg, "max_chars_per_msg", max_chars_per_msg)
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self.strategy = strategy
        self.summarize_fn = summarize_fn
        self.preserve_recent = preserve_recent
        self.max_chars_per_msg = max_chars_per_msg
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
        """Synchronous compaction using TRUNCATE strategy."""
        if not self.should_compact(messages):
            return CompactionResult(messages=messages)

        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self.preserve_recent:
            compacted = system_messages + [self._truncate(m) for m in non_system]
            return CompactionResult(messages=compacted, was_compacted=True, strategy_used="truncate")

        to_summarize = non_system[: -self.preserve_recent]
        keep_intact = non_system[-self.preserve_recent :]
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

    async def compact_async(self, messages: List[Dict[str, Any]]) -> CompactionResult:
        """Asynchronous compaction; uses LLM summarization when configured."""
        if not self.should_compact(messages):
            return CompactionResult(messages=messages)

        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self.preserve_recent:
            compacted = system_messages + [self._truncate(m) for m in non_system]
            return CompactionResult(messages=compacted, was_compacted=True, strategy_used="truncate")

        to_summarize = non_system[: -self.preserve_recent]
        keep_intact = non_system[-self.preserve_recent :]

        if self.strategy == SummarizationStrategy.LLM_SUMMARIZE and self.summarize_fn is not None:
            try:
                summary_text = await self.summarize_fn(to_summarize)
                summary = {
                    "role": "system",
                    "content": f"[Earlier conversation summary]:\n{summary_text}",
                }
                return CompactionResult(
                    messages=system_messages + [summary] + keep_intact,
                    was_compacted=True,
                    strategy_used="llm_summarize",
                    summary_text=summary_text,
                )
            except Exception as exc:
                logger.warning("LLM summarization failed, falling back to truncate: %s", exc)

        # Fallback to placeholder summary (previous behavior) or pure truncate
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

    def _truncate(self, message: Dict[str, Any], max_chars: Optional[int] = None) -> Dict[str, Any]:
        limit = max_chars if max_chars is not None else self.max_chars_per_msg
        content = message.get("content", "")
        if isinstance(content, str) and len(content) > limit:
            truncated = content[:limit] + f"\n\n[Truncated from {len(content)} chars]"
            return {**message, "content": truncated}
        return message
