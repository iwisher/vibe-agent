"""Context compaction module for managing conversation token limits."""

import asyncio
from dataclasses import dataclass
from typing import Optional, Protocol

from ..utils.token_counter import TokenCounter


class Summarizer(Protocol):
    """Protocol for message summarization."""

    async def summarize(self, messages: list[dict], max_length: int = 500) -> str:
        """Summarize a list of messages."""
        ...


@dataclass
class CompactionConfig:
    """Configuration for context compaction."""
    max_tokens: int = 100000
    target_tokens: int = 80000  # Compact to this level
    preserve_recent: int = 4  # Keep N most recent messages intact
    min_messages_to_compact: int = 10  # Only compact if more than this
    summary_max_tokens: int = 500


@dataclass
class CompactionResult:
    """Result of a compaction operation."""
    original_tokens: int
    compacted_tokens: int
    messages_summarized: int
    summary: str


class ContextCompactor:
    """Manages conversation context to stay within token limits."""

    def __init__(
        self,
        token_counter: Optional[TokenCounter] = None,
        summarizer: Optional[Summarizer] = None,
        config: Optional[CompactionConfig] = None,
        max_tokens: Optional[int] = None,
    ):
        self.token_counter = token_counter or TokenCounter()
        self.summarizer = summarizer
        
        # Support both config object and direct max_tokens parameter
        if config is not None:
            self.config = config
        elif max_tokens is not None:
            self.config = CompactionConfig(max_tokens=max_tokens, target_tokens=int(max_tokens * 0.8))
        else:
            self.config = CompactionConfig()

    def should_compact(self, messages: list) -> bool:
        """Check if compaction is needed."""
        if len(messages) < self.config.min_messages_to_compact:
            return False

        total_tokens = self._count_tokens(messages)
        return total_tokens > self.config.max_tokens

    def _count_tokens(self, messages: list) -> int:
        """Count total tokens in messages."""
        count = 0
        for msg in messages:
            content = getattr(msg, 'content', str(msg))
            count += self.token_counter.count(content)
        return count

    async def compact(self, messages: list) -> list:
        """Compact messages to reduce token usage."""
        if not self.should_compact(messages):
            return messages

        original_tokens = self._count_tokens(messages)

        # Preserve recent messages
        preserve_count = min(self.config.preserve_recent, len(messages) // 2)
        to_preserve = messages[-preserve_count:]
        to_compact = messages[:-preserve_count]

        # Create summary of older messages
        if self.summarizer and to_compact:
            message_dicts = [
                {"role": getattr(m, "role", "unknown"), "content": getattr(m, "content", str(m))}
                for m in to_compact
            ]
            summary = await self.summarizer.summarize(
                message_dicts,
                max_length=self.config.summary_max_tokens
            )
        else:
            # Basic truncation without summarizer
            summary = self._create_basic_summary(to_compact)

        # Create summary message
        from .query_loop import Message  # Avoid circular import
        summary_message = Message(
            role="system",
            content=f"[Earlier conversation summary]: {summary}"
        )

        # Combine: summary + preserved messages
        compacted = [summary_message] + list(to_preserve)

        compacted_tokens = self._count_tokens(compacted)

        result = CompactionResult(
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            messages_summarized=len(to_compact),
            summary=summary,
        )

        # Store result for later inspection
        self._last_result = result

        return compacted

    def _create_basic_summary(self, messages: list) -> str:
        """Create a basic summary without LLM."""
        user_msgs = [m for m in messages if getattr(m, "role", "") == "user"]
        assistant_msgs = [m for m in messages if getattr(m, "role", "") == "assistant"]

        topics = []
        for msg in user_msgs[-3:]:  # Last 3 user queries
            content = getattr(msg, "content", str(msg))
            # Extract first line or first 50 chars as topic
            topic = content.split("\n")[0][:50]
            if topic:
                topics.append(topic)

        summary_parts = [
            f"Conversation with {len(user_msgs)} user queries and {len(assistant_msgs)} assistant responses."
        ]
        if topics:
            summary_parts.append(f"Recent topics: {'; '.join(topics)}")

        return " ".join(summary_parts)

    async def compact_with_llm_summary(
        self,
        messages: list,
        llm_client,
    ) -> list:
        """Compact using LLM-based summarization."""

        class LLMSummarizer:
            def __init__(self, client):
                self.client = client

            async def summarize(self, messages: list[dict], max_length: int = 500) -> str:
                prompt = f"""Summarize the following conversation concisely (max {max_length} tokens).
Capture key decisions, actions taken, and current context. Be specific about:
- Files that were read or modified
- Tools that were used
- Key findings or conclusions

Conversation:
{self._format_messages(messages)}

Summary:"""

                response = await self.client.complete([{
                    "role": "user",
                    "content": prompt
                }])
                return response.content

            def _format_messages(self, messages: list[dict]) -> str:
                lines = []
                for m in messages:
                    role = m.get("role", "unknown")
                    content = m.get("content", "")
                    lines.append(f"{role}: {content[:200]}...")
                return "\n".join(lines)

        summarizer = LLMSummarizer(llm_client)
        original_summarizer = self.summarizer
        self.summarizer = summarizer

        try:
            return await self.compact(messages)
        finally:
            self.summarizer = original_summarizer

    def get_last_compaction_info(self) -> Optional[CompactionResult]:
        """Get information about the last compaction."""
        return getattr(self, "_last_result", None)
