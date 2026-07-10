"""Vector Index Upgrade — migrate PageIndex from fastText to sentence-transformers.

Provides transparent upgrade path with fallback and caching.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from vibe.memory.models import IndexNode
from vibe.memory.vector_index import KeywordIndex, VectorIndex

logger = logging.getLogger(__name__)


class UpgradedVectorIndex:
    """Wrapper that auto-upgrades from KeywordIndex to SentenceTransformerIndex.

    On first use, attempts to load sentence-transformers. If available,
    swaps the internal index. If not, keeps using KeywordIndex.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        cache_path: str | Path | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_path = Path(cache_path) if cache_path else None
        self._index: VectorIndex | None = None
        self._backend_name: str = "none"

    def _get_index(self) -> VectorIndex:
        """Lazy-load the best available index."""
        if self._index is not None:
            return self._index

        # Try sentence-transformers first
        try:
            from vibe.memory.vector_index import SentenceTransformerIndex

            self._index = SentenceTransformerIndex(
                model_name=self.model_name,
                cache_path=self.cache_path,
            )
            self._backend_name = "sentence_transformers"
            logger.info(f"Upgraded to SentenceTransformerIndex ({self.model_name})")
        except ImportError:
            logger.warning("sentence-transformers not available; using KeywordIndex fallback")
            self._index = KeywordIndex()
            self._backend_name = "keyword"

        return self._index

    @property
    def backend_name(self) -> str:
        """Return the active backend name."""
        _ = self._get_index()  # Ensure initialized
        return self._backend_name

    def encode(self, texts: list[str]) -> np.ndarray:
        return self._get_index().encode(texts)

    def search(self, query: str, nodes: list[IndexNode], top_k: int = 5) -> list[IndexNode]:
        return self._get_index().search(query, nodes, top_k)

    def save_cache(self) -> None:
        self._get_index().save_cache()


def upgrade_page_index(pageindex: Any) -> None:
    """Upgrade an existing PageIndex to use sentence-transformers.

    Args:
        pageindex: PageIndex instance to upgrade
    """
    if hasattr(pageindex, "set_vector_index"):
        upgraded = UpgradedVectorIndex()
        pageindex.set_vector_index(upgraded)
        logger.info(f"PageIndex upgraded to {upgraded.backend_name}")
    else:
        logger.warning("PageIndex does not support set_vector_index")
