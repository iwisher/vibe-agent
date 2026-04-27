"""Knowledge extraction from conversations — Phase 1b Gated Auto-Extraction.

Provides KnowledgeExtractor that:
- Extracts structured knowledge from completed conversation sessions
- Scores novelty against existing wiki pages via PageIndex
- Applies configurable quality gates (novelty + confidence thresholds)
- Never raises — all errors are caught and logged
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from vibe.memory.models import WikiPage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT_TEMPLATE = """You are a knowledge extraction engine. Analyze the following conversation and extract factual knowledge that should be preserved in a long-term wiki.

Instructions:
- Extract only concrete, factual information (not opinions, greetings, or small talk)
- Each item should be a self-contained knowledge nugget
- Use [[slug]] syntax to reference related concepts (e.g., [[python]], [[docker]])
- Include specific details: names, dates, versions, commands, URLs, decisions

For each knowledge item, provide:
- title: A concise, descriptive title (3-8 words)
- content: The knowledge content in markdown format (2-5 sentences)
- tags: 2-5 relevant tags as a list of strings
- citations: Source references as list of dicts with keys "session" and "message_index"

Respond with ONLY a JSON array. No markdown code fences, no extra text.

Example:
[
  {{
    "title": "Docker Compose Network Mode",
    "content": "Docker Compose supports `network_mode: host` to share the host's network namespace. This is useful for services that need to bind to specific ports without port mapping. See [[docker-compose]] for configuration details.",
    "tags": ["docker", "networking", "compose"],
    "citations": [{{"session": "abc123", "message_index": 5}}]
  }}
]

CONVERSATION:
{transcript}
"""

# ---------------------------------------------------------------------------
# KnowledgeExtractor
# ---------------------------------------------------------------------------


class KnowledgeExtractor:
    """Extract knowledge from conversation sessions and apply quality gates.

    Thread-safety: stateless — safe to use from multiple coroutines.
    Error policy: all public methods catch exceptions and return safe defaults.
    """

    def __init__(
        self,
        llm_client: Any,
        wiki: Any,
        pageindex: Any | None = None,
        flash_client: Any | None = None,
        config: Any | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.wiki = wiki
        self.pageindex = pageindex
        self.flash_client = flash_client
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract_from_session(
        self, messages: list[Any], session_id: str
    ) -> list[dict]:
        """Extract knowledge items from a conversation session.

        Args:
            messages: List of Message dataclasses with role, content attributes.
            session_id: UUID of the session for citation tracking.

        Returns:
            List of knowledge dicts: {title, content, tags, citations}.
            Empty list on any error (never raises).
        """
        try:
            transcript = self._build_transcript(messages, session_id)
            if not transcript.strip():
                logger.debug("Extraction skipped: empty transcript")
                return []

            prompt = _EXTRACTION_PROMPT_TEMPLATE.replace("{transcript}", transcript)
            response = await self._call_llm(prompt)
            if not response:
                return []

            items = self._parse_extraction_response(response, session_id)
            logger.debug("Extracted %d knowledge items from session %s", len(items), session_id)
            return items
        except Exception as e:
            logger.warning("Knowledge extraction failed (non-fatal): %s", e)
            return []

    async def score_novelty(self, items: list[dict]) -> list[float]:
        """Score how novel each item is compared to existing wiki pages.

        Returns list of floats 0.0-1.0 where 1.0 = entirely new.
        If PageIndex unavailable, returns all 1.0s.
        """
        if not items:
            return []
        if self.pageindex is None:
            return [1.0] * len(items)

        scores: list[float] = []
        for item in items:
            try:
                score = await self._score_single_novelty(item)
                scores.append(score)
            except Exception as e:
                logger.debug("Novelty scoring failed for item '%s': %s", item.get("title", ""), e)
                scores.append(1.0)  # Default to "novel" on error
        return scores

    async def apply_gates(
        self,
        items: list[dict],
        novelty_threshold: float = 0.5,
        confidence_threshold: float = 0.8,
    ) -> list[dict]:
        """Filter knowledge items through quality gates.

        Gates applied in order:
        1. Novelty threshold (skip if too similar to existing pages)
        2. Confidence threshold (skip if flash model scores confidence too low)

        Returns approved items only. Never raises.
        """
        if not items:
            return []

        # Gate 1: Novelty
        novelty_scores = await self.score_novelty(items)
        novel_items = [
            item for item, score in zip(items, novelty_scores) if score >= novelty_threshold
        ]
        rejected_novelty = len(items) - len(novel_items)
        if rejected_novelty > 0:
            logger.debug("Gated extraction: %d items rejected by novelty", rejected_novelty)

        # Gate 2: Confidence (if flash client available)
        if self.flash_client is None:
            return novel_items

        approved_items: list[dict] = []
        for item in novel_items:
            try:
                confidence = await self.flash_client.score_confidence(item.get("content", ""))
                if confidence >= confidence_threshold:
                    item["_confidence"] = confidence  # Attach for debugging
                    approved_items.append(item)
                else:
                    logger.debug(
                        "Gated extraction: item '%s' rejected by confidence %.2f < %.2f",
                        item.get("title", ""),
                        confidence,
                        confidence_threshold,
                    )
            except Exception as e:
                logger.debug("Confidence scoring failed for '%s': %s", item.get("title", ""), e)
                approved_items.append(item)  # Pass through on error

        return approved_items

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_transcript(self, messages: list[Any], session_id: str) -> str:
        """Build a formatted transcript from messages for the LLM prompt."""
        lines: list[str] = []
        for i, msg in enumerate(messages):
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
            if not content or not content.strip():
                continue
            # Skip system messages and tool results (too noisy)
            if role in ("system", "tool"):
                continue
            lines.append(f"[{i}] {role}: {content.strip()}")
        return "\n\n".join(lines)

    async def _call_llm(self, prompt: str) -> str | None:
        """Call the LLM with the extraction prompt. Returns raw response or None."""
        try:
            # Use the llm client's complete method if available
            if hasattr(self.llm_client, "complete"):
                response = await self.llm_client.complete(prompt)
                if hasattr(response, "content"):
                    return response.content
                if isinstance(response, str):
                    return response
            # Fallback: try chat-style interface
            if hasattr(self.llm_client, "chat"):
                response = await self.llm_client.chat([{"role": "user", "content": prompt}])
                if hasattr(response, "content"):
                    return response.content
                if isinstance(response, str):
                    return response
            logger.warning("LLM client has no compatible interface for extraction")
            return None
        except Exception as e:
            logger.warning("LLM extraction call failed: %s", e)
            return None

    def _parse_extraction_response(self, raw: str, session_id: str) -> list[dict]:
        """Parse LLM response into structured knowledge items."""
        if not raw or not raw.strip():
            return []

        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first line (```json or ```)
            if lines:
                lines = lines[1:]
            # Remove last line if it's ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.warning("Extraction response is not valid JSON: %s", e)
            return []

        if not isinstance(data, list):
            logger.warning("Extraction response is not a JSON array: %s", type(data))
            return []

        items: list[dict] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            item = {
                "title": str(entry.get("title", "")).strip(),
                "content": str(entry.get("content", "")).strip(),
                "tags": list(entry.get("tags", [])),
                "citations": list(entry.get("citations", [])),
            }
            if not item["title"] or not item["content"]:
                continue
            # Ensure citations have session
            for citation in item["citations"]:
                if isinstance(citation, dict) and "session" not in citation:
                    citation["session"] = session_id
            # If no citations provided, add a default one
            if not item["citations"]:
                item["citations"] = [{"session": session_id, "message_index": 0}]
            items.append(item)

        return items

    async def _score_single_novelty(self, item: dict) -> float:
        """Score novelty of a single item against existing wiki pages.

        Uses PageIndex route() to find similar pages, then computes
        a novelty score based on title/content similarity.
        """
        if self.pageindex is None:
            return 1.0

        # Try to find similar pages via PageIndex routing
        query = item.get("title", "") + " " + item.get("content", "")[:200]
        try:
            nodes = await self.pageindex.route(query)
        except Exception:
            return 1.0

        if not nodes:
            return 1.0  # No similar pages found = entirely novel

        # Check for near-duplicate titles
        item_title_lower = item.get("title", "").lower()
        for node in nodes[:3]:
            node_title = getattr(node, "title", "").lower()
            if node_title == item_title_lower:
                return 0.0  # Exact title match = duplicate
            # Simple word overlap check
            item_words = set(item_title_lower.split())
            node_words = set(node_title.split())
            if item_words and node_words:
                overlap = len(item_words & node_words) / max(len(item_words), len(node_words))
                if overlap > 0.8:
                    return 0.1  # Near-duplicate

        return 1.0  # Different enough
