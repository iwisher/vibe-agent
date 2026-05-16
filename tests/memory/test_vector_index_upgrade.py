"""Tests for vector index upgrade (fastText → sentence-transformers)."""

import pytest

from vibe.memory.models import IndexNode
from vibe.memory.vector_index import KeywordIndex
from vibe.memory.vector_index_upgrade import UpgradedVectorIndex, upgrade_page_index


class TestUpgradedVectorIndex:
    def test_fallback_to_keyword_when_sentence_transformers_unavailable(self):
        """When sentence-transformers is not installed, fall back to KeywordIndex."""
        idx = UpgradedVectorIndex()
        # Force re-init by clearing internal state
        idx._index = None
        # Mock the import to fail
        import sys
        real_module = sys.modules.get("vibe.memory.vector_index")
        original = None
        try:
            # Temporarily hide SentenceTransformerIndex
            if real_module:
                original = getattr(real_module, "SentenceTransformerIndex", None)
                if original:
                    delattr(real_module, "SentenceTransformerIndex")

            # This should fall back to KeywordIndex
            result = idx._get_index()
            assert isinstance(result, KeywordIndex)
            assert idx.backend_name == "keyword"
        finally:
            if real_module and original:
                setattr(real_module, "SentenceTransformerIndex", original)

    def test_backend_name_property(self):
        idx = UpgradedVectorIndex()
        name = idx.backend_name
        assert name in ("sentence_transformers", "keyword")

    def test_encode_empty(self):
        idx = UpgradedVectorIndex()
        result = idx.encode([])
        assert len(result) == 0

    def test_search_with_nodes(self):
        idx = UpgradedVectorIndex()
        # Force keyword fallback for deterministic test
        idx._index = KeywordIndex()
        idx._backend_name = "keyword"

        nodes = [
            IndexNode(
                node_id="n1",
                title="Python Programming",
                description="Learn Python",
                file_path="/tmp/python.md",
                tags=["python"],
            ),
            IndexNode(
                node_id="n2",
                title="Java Programming",
                description="Learn Java",
                file_path="/tmp/java.md",
                tags=["java"],
            ),
        ]
        results = idx.search("python", nodes, top_k=2)
        # Should return at least one result (KeywordIndex matches "python")
        assert len(results) >= 1
        assert results[0].title == "Python Programming"

    def test_save_cache_no_crash(self):
        idx = UpgradedVectorIndex()
        idx.save_cache()  # Should not raise even with no cache


class TestUpgradePageIndex:
    def test_upgrade_page_index(self):
        class FakePageIndex:
            def __init__(self):
                self.vector_index = None

            def set_vector_index(self, idx):
                self.vector_index = idx

        pageindex = FakePageIndex()
        upgrade_page_index(pageindex)
        assert pageindex.vector_index is not None
        assert isinstance(pageindex.vector_index, UpgradedVectorIndex)

    def test_upgrade_page_index_no_method(self):
        class FakePageIndex:
            pass

        pageindex = FakePageIndex()
        # Should not raise
        upgrade_page_index(pageindex)
