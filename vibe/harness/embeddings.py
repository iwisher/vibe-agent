"""Shared embedding utilities for vibe-agent.

Tries sentence-transformers (all-MiniLM-L6-v2, 384-dim) first, falls back to
fastText (cc.en.50.bin) for backward compatibility. Returns None if neither is
available so callers can fall back to keyword search.

Provides a singleton model loader, shared in-memory cache, and cosine similarity.
Both HybridPlanner and TraceStore should import from this module to ensure
consistent vector dimensions and avoid loading multiple embedding models.
"""

import hashlib
import os
import threading
from typing import Any, Optional

try:
    import numpy as np
except ImportError:
    np = None

try:
    import fasttext
except ImportError:
    fasttext = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

# Global singleton — loaded once, shared across components
_EMBEDDING_MODEL: Optional[Any] = None
_EMBEDDING_BACKEND: str = "none"  # "sentence_transformers" | "fasttext" | "none"
_EMBEDDING_LOCK = threading.Lock()

# Global cache — thread-safe via GIL (dict operations are atomic in CPython)
_EMBEDDING_CACHE: dict[str, list[float]] = {}
_EMBEDDING_CACHE_MAX_SIZE = 1000


def load_model(model_path: Optional[str] = None) -> Optional[Any]:
    """Load embedding model (singleton).

    Tries sentence-transformers first, then fastText, then returns None.
    Thread-safe: uses a lock to prevent concurrent model loading.

    Args:
        model_path: Path to fastText .bin file (only used for fastText fallback).

    Returns:
        Loaded model, or None if no backend is available.
    """
    global _EMBEDDING_MODEL, _EMBEDDING_BACKEND
    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL

    if np is None:
        return None

    with _EMBEDDING_LOCK:
        # Double-check after acquiring lock
        if _EMBEDDING_MODEL is not None:
            return _EMBEDDING_MODEL

        # 1. Try sentence-transformers (preferred)
        if SentenceTransformer is not None:
            try:
                _EMBEDDING_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
                _EMBEDDING_BACKEND = "sentence_transformers"
                return _EMBEDDING_MODEL
            except Exception:
                pass

        # 2. Fall back to fastText
        if fasttext is not None:
            path = model_path or os.getenv("FASTTEXT_MODEL_PATH", "cc.en.50.bin")
            if os.path.exists(path):
                try:
                    _EMBEDDING_MODEL = fasttext.load_model(path)
                    _EMBEDDING_BACKEND = "fasttext"
                    return _EMBEDDING_MODEL
                except Exception:
                    pass

    return None


def get_embedding(text: str, model_path: Optional[str] = None) -> Optional[list[float]]:
    """Get embedding vector for text.

    Uses sentence-transformers (384-dim) if available, otherwise fastText
    (50-dim). Results are cached in an LRU cache (maxsize=1000).

    Args:
        text: Input text to embed.
        model_path: Optional override for fastText model path.

    Returns:
        Float list (dimension depends on backend), or None if model unavailable
        or text is empty.
    """
    if not text or not text.strip():
        return None

    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _EMBEDDING_CACHE:
        # True LRU: move accessed item to end (newest)
        val = _EMBEDDING_CACHE.pop(cache_key)
        _EMBEDDING_CACHE[cache_key] = val
        return val

    model = load_model(model_path)
    if model is None:
        return None

    try:
        if _EMBEDDING_BACKEND == "sentence_transformers":
            vec = model.encode(text.strip(), convert_to_numpy=True)
            result = vec.tolist() if hasattr(vec, "tolist") else list(vec)
        else:
            vec = model.get_sentence_vector(text.strip().lower())
            result = vec.tolist() if hasattr(vec, "tolist") else list(vec)

        # LRU eviction when cache reaches capacity
        if len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAX_SIZE:
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
