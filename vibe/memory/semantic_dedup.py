"""Semantic deduplication for wiki pages using vector similarity.

Replaces simple Jaccard title-overlap with embedding-based similarity
for detecting pages about the same concept with different titles.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class VectorEmbedder(Protocol):
    """Protocol for text embedding providers."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class SemanticDeduplicator:
    """Find duplicate wiki pages using vector similarity.

    Falls back to Jaccard title-overlap if embedder is unavailable.
    """

    def __init__(
        self,
        embedder: VectorEmbedder | None = None,
        similarity_threshold: float = 0.85,
    ) -> None:
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold

    def _jaccard_similarity(self, a: str, b: str) -> float:
        """Word-level Jaccard similarity."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def find_duplicate(
        self,
        title: str,
        content: str | None = None,
        candidates: list[Any] | None = None,
    ) -> Any | None:
        """Find the most similar candidate page, or None if no match.

        Tries vector similarity first (if embedder available), then
        falls back to Jaccard title-overlap.
        """
        if not candidates:
            return None

        # Strategy 1: Vector similarity (if embedder available)
        if self.embedder is not None:
            try:
                query_text = title if content is None else f"{title}\n{content}"
                candidate_texts = []
                for c in candidates:
                    c_title = getattr(c, "title", "")
                    c_content = getattr(c, "content", "")
                    candidate_texts.append(c_title if not c_content else f"{c_title}\n{c_content}")

                all_embeddings = await self.embedder.embed([query_text] + candidate_texts)
                query_vec = all_embeddings[0]
                candidate_vecs = all_embeddings[1:]

                best_sim = -1.0
                best_candidate = None
                for candidate, vec in zip(candidates, candidate_vecs):
                    sim = self._cosine_similarity(query_vec, vec)
                    if sim > best_sim:
                        best_sim = sim
                        best_candidate = candidate

                if best_sim >= self.similarity_threshold:
                    logger.debug(
                        f"Semantic dedup: vector match {best_sim:.3f} >= "
                        f"{self.similarity_threshold}"
                    )
                    return best_candidate

            except Exception as e:
                logger.debug(f"Vector dedup failed, falling back to Jaccard: {e}")

        # Strategy 2: Jaccard title-overlap fallback
        best_jaccard = -1.0
        best_candidate = None
        for candidate in candidates:
            c_title = getattr(candidate, "title", "")
            sim = self._jaccard_similarity(title, c_title)
            if sim > best_jaccard:
                best_jaccard = sim
                best_candidate = candidate

        if best_jaccard >= 0.7:
            logger.debug(f"Semantic dedup: Jaccard match {best_jaccard:.3f} >= 0.7")
            return best_candidate

        return None

    async def is_duplicate(
        self,
        title: str,
        content: str | None = None,
        candidates: list[Any] | None = None,
    ) -> bool:
        """Check if a duplicate exists."""
        return await self.find_duplicate(title, content, candidates) is not None
