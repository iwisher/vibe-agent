"""Token counting utilities for context management."""

import re
from typing import List, Dict, Any


class TokenCounter:
    """Simple token counter for estimating token usage."""

    # Average characters per token for estimation
    CHARS_PER_TOKEN = 4

    def __init__(self):
        self.model = "gpt-4"

    def count_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        if not text:
            return 0
        # Simple estimation: ~4 characters per token
        return len(text) // self.CHARS_PER_TOKEN + (1 if len(text) % self.CHARS_PER_TOKEN else 0)

    def count(self, text: str) -> int:
        """Alias for count_tokens for compatibility."""
        return self.count_tokens(text)

    def count_message_tokens(self, message: Dict[str, Any]) -> int:
        """Count tokens in a message dict."""
        content = message.get("content", "")
        if isinstance(content, str):
            return self.count_tokens(content)
        elif isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict):
                    total += self.count_tokens(item.get("text", ""))
            return total
        return 0

    def count_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count total tokens in a list of messages."""
        return sum(self.count_message_tokens(m) for m in messages)

    def is_within_budget(self, messages: List[Dict[str, Any]], max_tokens: int) -> bool:
        """Check if messages are within token budget."""
        return self.count_messages_tokens(messages) <= max_tokens