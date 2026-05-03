"""Vector Index Layer for PageIndex.

Provides a protocol for vector-based semantic search with two implementations:
- KeywordIndex: A no-dependency fallback that uses simple word overlap.
- SentenceTransformerIndex: A full dense-vector implementation using sentence-transformers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from vibe.memory.models import IndexNode

logger = logging.getLogger(__name__)


class VectorIndex(Protocol):
    """Protocol for vector indexing and search over wiki pages."""

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode a list of texts into dense vectors."""
        ...

    def search(
        self, query: str, nodes: list[IndexNode], top_k: int = 5
    ) -> list[IndexNode]:
        """Search across nodes using the given query."""
        ...

    def save_cache(self) -> None:
        """Save any cached embeddings to disk."""
        ...


class KeywordIndex:
    """Fallback index that uses simple keyword overlap matching (no ML dependencies)."""

    def encode(self, texts: list[str]) -> np.ndarray:
        # We don't actually encode dense vectors
        return np.zeros((len(texts), 1), dtype=np.float32)

    def search(
        self, query: str, nodes: list[IndexNode], top_k: int = 5
    ) -> list[IndexNode]:
        q = query.lower().split()
        scored: list[tuple[float, IndexNode]] = []

        for node in nodes:
            if not node.file_path:
                continue

            score = 0.0
            text = f"{node.title} {node.description} {' '.join(node.tags)}".lower()
            for word in q:
                if len(word) > 2 and word in text:
                    score += 1.0

            if score > 0:
                # Need to return a copy with confidence set
                node_copy = IndexNode(
                    node_id=node.node_id,
                    title=node.title,
                    description=node.description,
                    file_path=node.file_path,
                    tags=node.tags,
                    confidence=min(1.0, score / max(1, len(q))),
                )
                scored.append((score, node_copy))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [n for _, n in scored[:top_k]]

    def save_cache(self) -> None:
        pass


class SentenceTransformerIndex:
    """Dense vector index using sentence-transformers.

    Lazy loads the model on first use. Caches embeddings to a .npy file.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", cache_path: str | Path | None = None) -> None:
        self.model_name = model_name
        if cache_path:
            self.cache_path = Path(cache_path)
            # Normalize to .npz for savez format (no pickle)
            if self.cache_path.suffix not in (".npz", ".npy"):
                self.cache_path = self.cache_path.with_suffix(".npz")
            elif self.cache_path.suffix == ".npy":
                self.cache_path = self.cache_path.with_suffix(".npz")
        else:
            self.cache_path = None

        self._model: Any = None

        # map node_id -> embedding (np.ndarray)
        self._cache: dict[str, np.ndarray] = {}
        self._cache_loaded = False

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            import torch
            from sentence_transformers import SentenceTransformer

            # Simple device selection
            device = "cpu"
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"

            self._model = SentenceTransformer(self.model_name, device=device)
            logger.debug(f"Loaded SentenceTransformer {self.model_name} on {device}")
            return self._model
        except ImportError:
            logger.error("sentence-transformers is not installed. Run: pip install vibe-agent[memory]")
            raise

    def _load_cache(self) -> None:
        if self._cache_loaded or not self.cache_path:
            return

        if self.cache_path.exists():
            try:
                # Load dictionary of arrays using np.savez format (no pickle)
                with np.load(self.cache_path) as data:
                    self._cache = dict(data)
                    logger.debug(f"Loaded {len(self._cache)} embeddings from cache.")
            except Exception as e:
                logger.warning(f"Failed to load embedding cache from {self.cache_path}: {e}")

        self._cache_loaded = True

    def save_cache(self) -> None:
        """Save the current embedding cache to disk."""
        if not self.cache_path or not self._cache:
            return

        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.cache_path, **self._cache)
        except Exception as e:
            logger.warning(f"Failed to save embedding cache to {self.cache_path}: {e}")

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.array([])

        model = self._load_model()
        return model.encode(texts, convert_to_numpy=True)

    def _get_node_text(self, node: IndexNode) -> str:
        return f"{node.title}\n{node.description}\nTags: {', '.join(node.tags)}"

    def search(
        self, query: str, nodes: list[IndexNode], top_k: int = 5
    ) -> list[IndexNode]:
        """Search using cosine similarity on node embeddings."""
        if not nodes:
            return []

        self._load_cache()
        model = self._load_model()

        # 1. Encode the query
        query_emb = model.encode(query, convert_to_numpy=True)
        # normalize query
        query_norm = np.linalg.norm(query_emb)
        if query_norm > 0:
            query_emb = query_emb / query_norm

        # 2. Gather embeddings for all nodes, encoding if missing
        missing_nodes = []
        missing_texts = []

        for node in nodes:
            if not node.file_path:
                continue
            if node.node_id not in self._cache:
                missing_nodes.append(node)
                missing_texts.append(self._get_node_text(node))

        if missing_nodes:
            new_embs = model.encode(missing_texts, convert_to_numpy=True)
            for node, emb in zip(missing_nodes, new_embs):
                self._cache[node.node_id] = emb

            # Save cache if we computed new embeddings
            self.save_cache()

        # 3. Compute similarities
        scored_nodes = []
        for node in nodes:
            if not node.file_path:
                continue

            emb = self._cache.get(node.node_id)
            if emb is None:
                continue

            emb_norm = np.linalg.norm(emb)
            if emb_norm > 0:
                emb = emb / emb_norm

            score = float(np.dot(query_emb, emb))

            if score > 0.65:  # Minimum similarity threshold (MiniLM)
                node_copy = IndexNode(
                    node_id=node.node_id,
                    title=node.title,
                    description=node.description,
                    file_path=node.file_path,
                    tags=node.tags,
                    confidence=min(1.0, score),
                )
                scored_nodes.append((score, node_copy))

        # 4. Sort and return top_k
        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        return [n for _, n in scored_nodes[:top_k]]


def get_vector_index(model_name: str, cache_path: str | Path | None = None) -> VectorIndex:
    """Factory to get the best available VectorIndex."""
    try:
        import sentence_transformers  # noqa
        return SentenceTransformerIndex(model_name=model_name, cache_path=cache_path)
    except ImportError:
        logger.debug("sentence-transformers not installed; falling back to KeywordIndex.")
        return KeywordIndex()
