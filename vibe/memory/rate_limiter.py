"""Token Bucket rate limiter — placeholder for future RLM use (Phase 2).

Implements a simple token bucket algorithm for rate limiting LLM API calls.
Reserved for Phase 2 RLM Engine integration.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Token bucket rate limiter for LLM API calls.

    Usage:
        bucket = TokenBucket(rpm=60, tpm=100_000)
        await bucket.acquire(tokens=500)  # Wait until capacity available
    """

    def __init__(
        self,
        rpm: int = 60,       # requests per minute
        tpm: int = 100_000,  # tokens per minute
    ) -> None:
        self.rpm = rpm
        self.tpm = tpm

        # Request bucket
        self._req_tokens = float(rpm)
        self._req_last = time.monotonic()
        self._req_rate = rpm / 60.0  # tokens per second

        # Token bucket
        self._tok_tokens = float(tpm)
        self._tok_last = time.monotonic()
        self._tok_rate = tpm / 60.0

        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire capacity for a request using `tokens` output tokens."""
        async with self._lock:
            now = time.monotonic()

            # Refill request bucket
            elapsed = now - self._req_last
            self._req_tokens = min(self.rpm, self._req_tokens + elapsed * self._req_rate)
            self._req_last = now

            # Refill token bucket
            elapsed_tok = now - self._tok_last
            self._tok_tokens = min(self.tpm, self._tok_tokens + elapsed_tok * self._tok_rate)
            self._tok_last = now

            # Wait for request capacity
            if self._req_tokens < 1.0:
                wait = (1.0 - self._req_tokens) / self._req_rate
                await asyncio.sleep(wait)
                self._req_tokens = 0.0
            else:
                self._req_tokens -= 1.0

            # Wait for token capacity
            tok_needed = min(tokens, self.tpm)
            if self._tok_tokens < tok_needed:
                wait = (tok_needed - self._tok_tokens) / self._tok_rate
                await asyncio.sleep(wait)
                self._tok_tokens = 0.0
            else:
                self._tok_tokens -= tok_needed
