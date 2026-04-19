"""Context management with compaction for token budget enforcement."""

import json
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from ..utils.token_counter import TokenCounter


@dataclass
class CompactionResult:
    """Result of a compaction operation."""
    original_tokens: int
    compacted_tokens: int
    messages_removed: int
    summary: str
    working_semantics_preserved: bool


class ContextCompaction:
    """Compacts conversation context to fit within token budget."""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.token_counter = TokenCounter()

    def create_summary(
        self,
        messages: List[Dict[str, Any]],
        preserve_recent: int = 2
    ) -> str:
        """Create a summary of messages while preserving working semantics.

        This preserves:
        - Key facts and decisions
        - File paths and code references
        - User preferences expressed
        - TODO items or action items
        """
        if not messages:
            return ""

        # Extract messages to summarize (excluding recent ones)
        to_summarize = messages[:-preserve_recent] if len(messages) > preserve_recent else []

        if not to_summarize:
            return ""

        # Build summary text
        summary_parts = []
        summary_parts.append("## Previous Context Summary\n")

        # Extract key information
        key_facts = []
        files_mentioned = set()
        decisions = []
        action_items = []

        for msg in to_summarize:
            content = msg.get("content", "")
            if not content:
                continue

            # Look for file paths
            import re
            file_patterns = re.findall(r'[\w\-/\\]+\.[\w]+', content)
            files_mentioned.update(file_patterns)

            # Look for code blocks
            code_blocks = re.findall(r'```[\w]*\n(.*?)```', content, re.DOTALL)
            for block in code_blocks:
                # Keep short code snippets
                if len(block) < 200:
                    key_facts.append(f"Code: {block[:100]}...")

            # Check for decision keywords
            decision_keywords = ["decided", "agreed", "concluded", "chose", "selected"]
            for keyword in decision_keywords:
                if keyword in content.lower():
                    sentences = content.split('.')
                    for sent in sentences:
                        if keyword in sent.lower():
                            decisions.append(sent.strip())
                            break

            # Check for action items
            action_keywords = ["todo", "todo:", "action:", "need to", "should", "must"]
            for keyword in action_keywords:
                if keyword in content.lower():
                    lines = content.split('\n')
                    for line in lines:
                        if keyword in line.lower():
                            action_items.append(line.strip())

        # Build structured summary
        if files_mentioned:
            summary_parts.append("**Files referenced:** " + ", ".join(sorted(files_mentioned)[:10]))

        if decisions:
            summary_parts.append("\n**Decisions made:**")
            for d in decisions[:5]:
                summary_parts.append(f"- {d}")

        if action_items:
            summary_parts.append("\n**Action items:**")
            for item in action_items[:5]:
                summary_parts.append(f"- {item}")

        if key_facts:
            summary_parts.append("\n**Key facts:**")
            for fact in key_facts[:5]:
                summary_parts.append(f"- {fact}")

        return "\n".join(summary_parts)

    def compact(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        preserve_recent: int = 2
    ) -> CompactionResult:
        """Compact messages to fit within token budget.

        Strategy:
        1. Calculate current token count
        2. If over budget, summarize older messages
        3. Keep recent messages intact for working semantics
        4. Return compacted message list
        """
        original_tokens = self.token_counter.count_messages_tokens(messages)

        # If within budget, no compaction needed
        if original_tokens <= max_tokens:
            return CompactionResult(
                original_tokens=original_tokens,
                compacted_tokens=original_tokens,
                messages_removed=0,
                summary="No compaction needed",
                working_semantics_preserved=True
            )

        # Create summary of older messages
        summary = self.create_summary(messages, preserve_recent=preserve_recent)

        # Build new message list
        compacted = []

        # Add summary as system message if we have one
        if summary:
            compacted.append({
                "role": "system",
                "content": f"[Context Compacted]\n{summary}"
            })

        # Add preserved recent messages
        recent_messages = messages[-preserve_recent:] if len(messages) > preserve_recent else messages
        compacted.extend(recent_messages)

        compacted_tokens = self.token_counter.count_messages_tokens(compacted)

        messages_removed = len(messages) - len(recent_messages)

        return CompactionResult(
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            messages_removed=messages_removed,
            summary=summary,
            working_semantics_preserved=messages_removed > 0
        )


class ContextManager:
    """Manages conversation context with compaction."""

    DEFAULT_MAX_TOKENS = 8000
    COMPACTION_THRESHOLD = 0.8  # Compact when at 80% of budget

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        llm_client=None
    ):
        self.max_tokens = max_tokens
        self.compaction_threshold = int(max_tokens * self.COMPACTION_THRESHOLD)
        self.token_counter = TokenCounter()
        self.compaction = ContextCompaction(llm_client)
        self._compaction_history: List[CompactionResult] = []

    def check_and_compact(
        self,
        messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], Optional[CompactionResult]]:
        """Check if compaction is needed and perform it.

        Returns:
            (messages, compaction_result) - either original or compacted messages
        """
        token_count = self.token_counter.count_messages_tokens(messages)

        if token_count <= self.compaction_threshold:
            # No compaction needed
            return messages, None

        # Perform compaction
        result = self.compaction.compact(messages, self.max_tokens)

        if result.messages_removed > 0:
            self._compaction_history.append(result)

            # Build actual compacted message list
            compacted = []
            if result.summary:
                compacted.append({
                    "role": "system",
                    "content": f"[Context Compacted]\n{result.summary}"
                })
            # Preserve last 2 messages
            recent = messages[-2:] if len(messages) > 2 else messages
            compacted.extend(recent)

            return compacted, result

        return messages, None

    def get_stats(self) -> Dict[str, Any]:
        """Get context management statistics."""
        return {
            "max_tokens": self.max_tokens,
            "compaction_threshold": self.compaction_threshold,
            "compaction_count": len(self._compaction_history),
            "total_tokens_saved": sum(
                r.original_tokens - r.compacted_tokens
                for r in self._compaction_history
            ),
            "compaction_history": [
                {
                    "original_tokens": r.original_tokens,
                    "compacted_tokens": r.compacted_tokens,
                    "messages_removed": r.messages_removed
                }
                for r in self._compaction_history
            ]
        }

    def force_compaction(self, messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], CompactionResult]:
        """Force compaction regardless of token count."""
        result = self.compaction.compact(messages, self.max_tokens)

        compacted = []
        if result.summary:
            compacted.append({
                "role": "system",
                "content": f"[Context Compacted]\n{result.summary}"
            })
        recent = messages[-2:] if len(messages) > 2 else messages
        compacted.extend(recent)

        self._compaction_history.append(result)
        return compacted, result
