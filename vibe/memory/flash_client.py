"""FlashLLMClient — Cheap-model routing contract for quality gates.

Provides a lightweight client that routes to a "flash" (cheap/fast) model
for quality gate operations like contradiction detection and confidence scoring.

If no flash model is configured, operations gracefully skip with a warning log.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FlashLLMResponse:
    """Response from a flash LLM call."""

    def __init__(self, content: str, success: bool = True, error: str | None = None) -> None:
        self.content = content
        self.success = success
        self.error = error


class FlashLLMClient:
    """Cheap-model routing client for quality gate operations.

    Routes to a low-cost model (local Ollama, API flash tier, etc.)
    for operations like:
    - Contradiction detection between wiki pages
    - Novelty scoring for auto-extraction
    - Confidence threshold checking

    If unavailable, all operations return None/False and log a warning.
    The system continues without quality gates rather than failing.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "phi3:mini",
        api_key: str | None = None,
        timeout: float = 15.0,
        llm_client: Any | None = None,
    ) -> None:
        """Initialize FlashLLMClient.

        Args:
            base_url: Base URL for the flash model API (default: Ollama)
            model: Model name (default: phi3:mini — small, fast local model)
            api_key: Optional API key
            timeout: Request timeout in seconds
            llm_client: Optional pre-built LLM client to reuse
        """
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._llm_client = llm_client
        self._available: bool | None = None  # None = not yet checked

    async def check_available(self) -> bool:
        """Check if the flash model is reachable."""
        if self._available is not None:
            return self._available

        try:
            resp = await self.complete("ping", timeout=3.0)
            self._available = resp.success
        except Exception:
            self._available = False
            logger.warning(
                "FlashLLMClient: flash model not available at %s (model: %s). "
                "Quality gates (contradiction detection) will be skipped.",
                self.base_url,
                self.model,
            )
        return self._available

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        timeout: float | None = None,
    ) -> FlashLLMResponse:
        """Send a prompt to the flash model and return the response."""
        actual_timeout = timeout or self.timeout

        try:
            if self._llm_client is not None:
                # Use injected client
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                if asyncio.iscoroutinefunction(self._llm_client.complete):
                    resp = await asyncio.wait_for(
                        self._llm_client.complete(messages),
                        timeout=actual_timeout,
                    )
                else:
                    loop = asyncio.get_event_loop()
                    resp = await asyncio.wait_for(
                        loop.run_in_executor(None, self._llm_client.complete, messages),
                        timeout=actual_timeout,
                    )

                content = resp.content if hasattr(resp, "content") else str(resp)
                return FlashLLMResponse(content=content)

            # Direct HTTP call to Ollama-compatible API
            import json
            import httpx

            payload = {
                "model": self.model,
                "messages": [
                    *(
                        [{"role": "system", "content": system}]
                        if system
                        else []
                    ),
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            }
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=actual_timeout) as client:
                url = self.base_url.rstrip("/") + "/chat/completions"
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return FlashLLMResponse(content=content)

        except asyncio.TimeoutError:
            logger.debug("FlashLLMClient: timeout after %.1fs", actual_timeout)
            return FlashLLMResponse(content="", success=False, error="timeout")
        except Exception as e:
            logger.debug("FlashLLMClient: error: %s", e)
            return FlashLLMResponse(content="", success=False, error=str(e))

    async def detect_contradiction(
        self, new_content: str, existing_pages_content: list[str]
    ) -> bool:
        """Check if new_content contradicts any existing wiki pages.

        Returns True if contradiction detected (caller keeps new page as draft).
        Returns False if no contradiction or flash model unavailable.
        """
        if not await self.check_available():
            return False  # Skip with warning already logged in check_available

        if not existing_pages_content:
            return False

        existing_summary = "\n\n---\n\n".join(existing_pages_content[:3])
        prompt = f"""You are a fact-checker. Determine if the NEW CONTENT contradicts any of the EXISTING PAGES.

EXISTING PAGES:
{existing_summary}

NEW CONTENT:
{new_content}

Does the new content contain factual contradictions with the existing pages?
Answer with ONLY "yes" or "no"."""

        response = await self.complete(prompt, timeout=10.0)
        if not response.success:
            return False

        answer = response.content.strip().lower()
        return answer.startswith("yes")

    async def score_confidence(self, content: str) -> float:
        """Score how confident the LLM is about the factual accuracy of content.

        Returns a float 0.0-1.0, or 0.0 if flash model unavailable.
        """
        if not await self.check_available():
            return 0.0

        prompt = f"""Rate the factual accuracy and reliability of this content on a scale of 0.0 to 1.0.
Consider: specificity, internal consistency, presence of concrete evidence.

CONTENT:
{content[:2000]}

Respond with ONLY a decimal number between 0.0 and 1.0."""

        response = await self.complete(prompt, timeout=10.0)
        if not response.success:
            return 0.0

        try:
            return max(0.0, min(1.0, float(response.content.strip())))
        except ValueError:
            return 0.0
