"""Tests for shared embedding module."""

import pytest
from vibe.harness.embeddings import (
    get_embedding,
    cosine_similarity,
    clear_cache,
    cache_size,
    load_model,
)


class TestEmbeddings:
    """Tests for shared embedding utilities."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def test_get_embedding_returns_50_dim(self):
        """fastText embeddings should be 50-dimensional."""
        emb = get_embedding("hello world")
        if emb is not None:
            assert len(emb) == 50
            assert all(isinstance(v, float) for v in emb)

    def test_get_embedding_caches_results(self):
        """Same text should return cached result."""
        text = "test caching"
        emb1 = get_embedding(text)
        emb2 = get_embedding(text)
        if emb1 is not None:
            assert emb1 is emb2  # Same object (cached)
            assert cache_size() == 1

    def test_get_embedding_different_texts(self):
        """Different texts should have different embeddings."""
        emb1 = get_embedding("hello")
        emb2 = get_embedding("world")
        if emb1 is not None and emb2 is not None:
            assert emb1 != emb2

    def test_get_embedding_empty_text(self):
        """Empty text should return None."""
        assert get_embedding("") is None
        assert get_embedding("   ") is None

    def test_cosine_similarity_identical(self):
        """Identical vectors should have similarity 1.0."""
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self):
        """Orthogonal vectors should have similarity 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_cosine_similarity_opposite(self):
        """Opposite vectors should have similarity -1.0."""
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_cosine_similarity_mismatched_dims(self):
        """Mismatched dimensions should return 0.0."""
        assert cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0

    def test_cosine_similarity_empty_vectors(self):
        """Empty vectors should return 0.0."""
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([1.0], []) == 0.0

    def test_clear_cache(self):
        """clear_cache() should empty the cache."""
        emb = get_embedding("test")
        if emb is not None:
            assert cache_size() >= 1
            clear_cache()
            assert cache_size() == 0
        else:
            # Model not available — just verify clear_cache doesn't crash
            clear_cache()
            assert cache_size() == 0

    def test_load_model_singleton(self):
        """load_model should return the same instance on repeated calls."""
        m1 = load_model()
        m2 = load_model()
        assert m1 is m2  # Same singleton

    def test_embedding_semantic_similarity(self):
        """Similar words should have higher cosine similarity."""
        emb1 = get_embedding("king")
        emb2 = get_embedding("queen")
        emb3 = get_embedding("apple")
        if emb1 is not None and emb2 is not None and emb3 is not None:
            sim_king_queen = cosine_similarity(emb1, emb2)
            sim_king_apple = cosine_similarity(emb1, emb3)
            # King and queen are more similar than king and apple
            assert sim_king_queen > sim_king_apple
