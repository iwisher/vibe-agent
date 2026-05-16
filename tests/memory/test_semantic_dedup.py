"""Tests for semantic deduplication."""

import pytest

from vibe.memory.semantic_dedup import SemanticDeduplicator


class FakeEmbedder:
    """Fake embedder that returns deterministic embeddings."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Simple bag-of-words embedding with overlap
        embeddings = []
        for text in texts:
            words = set(text.lower().split())
            # Create a 100-dim vector where each word maps to a position
            vec = [0.0] * 100
            for w in words:
                # Deterministic hash to position
                pos = hash(w) % 100
                vec[pos] = 1.0
            embeddings.append(vec)
        return embeddings


class FakePage:
    def __init__(self, title, content=""):
        self.title = title
        self.content = content


class TestSemanticDeduplicator:
    def test_jaccard_similarity_exact(self):
        d = SemanticDeduplicator()
        assert d._jaccard_similarity("hello world", "hello world") == 1.0

    def test_jaccard_similarity_partial(self):
        d = SemanticDeduplicator()
        sim = d._jaccard_similarity("hello world foo", "hello world bar")
        assert 0.4 < sim < 0.6  # 2/3 overlap

    def test_jaccard_similarity_none(self):
        d = SemanticDeduplicator()
        assert d._jaccard_similarity("hello", "world") == 0.0

    def test_cosine_similarity_identical(self):
        d = SemanticDeduplicator()
        vec = [1.0, 2.0, 3.0]
        assert d._cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self):
        d = SemanticDeduplicator()
        assert d._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_vector_match(self):
        embedder = FakeEmbedder()
        d = SemanticDeduplicator(embedder=embedder, similarity_threshold=0.5)

        candidates = [
            FakePage("Python Programming", "Learn Python"),
            FakePage("Java Programming", "Learn Java"),
        ]

        # "Python Coding Learn Python" shares words with "Python Programming Learn Python"
        result = await d.find_duplicate("Python Coding", "Learn Python", candidates)
        assert result is not None
        assert result.title == "Python Programming"

    @pytest.mark.asyncio
    async def test_no_match_below_threshold(self):
        embedder = FakeEmbedder()
        d = SemanticDeduplicator(embedder=embedder, similarity_threshold=0.99)

        candidates = [FakePage("Java Programming", "Learn Java")]
        result = await d.find_duplicate("Python Coding", "Learn Python", candidates)
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_to_jaccard_when_no_embedder(self):
        d = SemanticDeduplicator(embedder=None)

        candidates = [FakePage("Python Programming Guide")]
        result = await d.find_duplicate("Python Programming Guide", candidates=candidates)
        assert result is not None
        assert result.title == "Python Programming Guide"

    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        d = SemanticDeduplicator()
        result = await d.find_duplicate("test", candidates=[])
        assert result is None

    @pytest.mark.asyncio
    async def test_is_duplicate(self):
        d = SemanticDeduplicator(embedder=None)
        candidates = [FakePage("Python Programming")]
        assert await d.is_duplicate("Python Programming", candidates=candidates) is True
        assert await d.is_duplicate("Java Programming", candidates=candidates) is False
