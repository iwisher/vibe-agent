"""Shared embedding utilities for vibe-agent.

Uses fastText cc.en.50.bin (50-dim vectors, ~5MB) as the standard embedding model.
Provides a singleton model loader, shared in-memory cache, and cosine similarity.

Both HybridPlanner and TraceStore should import from this module to ensure
consistent vector dimensions and avoid loading multiple embedding models.
"""

import hashlib
import os
from typing import Any, Optional

try:
    import numpy as np
except ImportError:
    np = None

try:
    import fasttext
except ImportError:
    fasttext = None

# Global singleton — loaded once, shared across components
_EMBEDDING_MODEL: Optional[Any] = None
_EMBEDDING_CACHE: dict[str, list[float]] = {}
_EMBEDDING_CACHE_MAX_SIZE = 1000


def load_model(model_path: Optional[str] = None) -> Optional[Any]:
    """Load fastText model (singleton).

    Args:
        model_path: Path to fastText .bin file. If None, uses
            FASTTEXT_MODEL_PATH env var or defaults to cc.en.50.bin.

    Returns:
        Loaded fastText model, or None if fasttext is not installed
        or the model file is not found.
    """
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL
    if fasttext is None or np is None:
        return None
    path = model_path or os.getenv("FASTTEXT_MODEL_PATH", "cc.en.50.bin")
    if not os.path.exists(path):
        return None
    try:
        _EMBEDDING_MODEL = fasttext.load_model(path)
        return _EMBEDDING_MODEL
    except Exception:
        return None


def get_embedding(text: str, model_path: Optional[str] = None) -> Optional[list[float]]:
    """Get 50-dim fastText embedding for text.

    Computes a word-level average of fastText vectors. Results are cached
    in an LRU cache (maxsize=1000) to avoid recomputing embeddings.

    Args:
        text: Input text to embed.
        model_path: Optional override for fastText model path.

    Returns:
        50-dim float list, or None if model unavailable or text is empty.
    """
    if not text or not text.strip():
        return None

    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[cache_key]

    model = load_model(model_path)
    if model is None:
        return None

    try:
        vec = model.get_sentence_vector(text.strip().lower())
        result = vec.tolist() if hasattr(vec, "tolist") else list(vec)
        # LRU eviction when cache reaches capacity
        if len(_EMBEDDING_CACHE) >= 1000:
            oldest = next(iter(_EMBEDDING_CACHE))
            del _EMBEDDING_CACHE[oldest]
        _EMBEDDING_CACHE[cache_key] = result
        return result
    except Exception:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector (must be same length as b).
        b: Second vector.

    Returns:
        Cosine similarity in range [-1, 1], or 0.0 if either vector
        is empty, has zero norm, or dimensions mismatch.
    """
    if not a or not b or len(a) != len(b) or np is None:
        return 0.0
    a_arr = np.array(a)
    b_arr = np.array(b)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


def clear_cache() -> None:
    """Clear the embedding cache. Useful for testing."""
    _EMBEDDING_CACHE.clear()


def cache_size() -> int:
    """Return current number of cached embeddings."""
    return len(_EMBEDDING_CACHE)
