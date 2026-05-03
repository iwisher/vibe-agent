"""Tests for Vector Index implementations."""

import numpy as np
import pytest

from vibe.memory.models import IndexNode
from vibe.memory.vector_index import KeywordIndex, SentenceTransformerIndex, get_vector_index
from vibe.memory.pageindex import PageIndex


def make_node(node_id: str, title: str, desc: str, tags: list[str]) -> IndexNode:
    return IndexNode(
        node_id=node_id,
        title=title,
        description=desc,
        file_path=f"/wiki/{node_id}.md",
        tags=tags,
    )


def test_keyword_index():
    nodes = [
        make_node("n1", "Database Scaling", "performance", ["db"]),
        make_node("n2", "UI Design", "buttons and forms", ["ui"]),
    ]
    idx = KeywordIndex()
    res = idx.search("database forms", nodes)
    assert len(res) == 2
    # Both should match since 'database' hits n1 and 'forms' hits n2
    
    res = idx.search("scaling", nodes)
    assert len(res) == 1
    assert res[0].node_id == "n1"


def test_sentence_transformer_paraphrase_match(monkeypatch):
    """Paraphrase queries should match semantically similar nodes."""
    import numpy as np

    class MockModel:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, texts, convert_to_numpy=True):
            # Deterministic 384-dim mock embeddings where related topics cluster
            if isinstance(texts, str):
                texts = [texts]
            vecs = []
            for t in texts:
                tlower = t.lower()
                # Base vectors for topic clusters
                if "rust" in tlower or "programming" in tlower:
                    base = np.array([1.0, 0.8] + [0.0] * 382, dtype=np.float32)
                elif "banana" in tlower or "smoothie" in tlower or "food" in tlower:
                    base = np.array([0.0, 1.0] + [0.0] * 382, dtype=np.float32)
                else:
                    base = np.zeros(384, dtype=np.float32)
                # Small noise per text
                h = hash(tlower.strip()) % 1000
                noise = np.random.RandomState(h).randn(384).astype(np.float32) * 0.01
                vec = base + noise
                vecs.append(vec)
            return np.array(vecs) if len(vecs) > 1 else vecs[0]

    monkeypatch.setattr(
        "vibe.memory.vector_index.SentenceTransformerIndex._load_model",
        lambda self: MockModel(),
    )

    idx = SentenceTransformerIndex()
    nodes = [
        make_node("n1", "Rust Programming Guide", "How to write Rust code", ["rust", "programming"]),
        make_node("n2", "Banana Smoothie Recipe", "Make a tasty banana drink", ["food"]),
    ]

    # "how to write rust" should match "Rust Programming Guide" via paraphrase
    res = idx.search("how to write rust", nodes)
    titles = [n.title for n in res]
    assert "Rust Programming Guide" in titles


def test_get_vector_index_fallback():
    # Force ImportError context or just assume it works
    # It should return SentenceTransformerIndex if installed, else KeywordIndex
    idx = get_vector_index("all-MiniLM-L6-v2")
    assert isinstance(idx, (KeywordIndex, SentenceTransformerIndex))


def test_sentence_transformer_index_mocked(tmp_path, monkeypatch):
    """Test ST index with mocked encoding to avoid heavy ML deps."""
    
    class MockModel:
        def __init__(self, *args, **kwargs):
            pass
            
        def encode(self, texts, convert_to_numpy=True):
            if isinstance(texts, str):
                return np.random.randn(384).astype(np.float32)
            # Just return random vectors
            return np.random.randn(len(texts), 384).astype(np.float32)
            
    monkeypatch.setattr("vibe.memory.vector_index.SentenceTransformerIndex._load_model", lambda self: MockModel())
    
    cache_file = tmp_path / "cache.npy"
    idx = SentenceTransformerIndex(cache_path=cache_file)
    
    nodes = [make_node("n1", "A", "B", ["c"])]
    res = idx.search("query", nodes)
    
    assert len(res) <= 1
    
    idx.save_cache()
    # Cache is saved as .npz (np.savez format), not .npy
    cache_file_npz = tmp_path / "cache.npz"
    assert cache_file_npz.exists()
    
    # Reload from cache
    idx2 = SentenceTransformerIndex(cache_path=cache_file_npz)
    monkeypatch.setattr("vibe.memory.vector_index.SentenceTransformerIndex._load_model", lambda self: MockModel())
    
    idx2._load_cache()
    assert "n1" in idx2._cache


@pytest.mark.asyncio
async def test_pageindex_uses_vector_index(tmp_path):
    idx = PageIndex(index_path=tmp_path / "idx.json", llm_client=None)
    idx.load()
    idx.add_node("root_01", "Database", "stuff", tags=["database"], file_path="/db")
    
    # Keyword index
    res = await idx.route("database")
    assert len(res) == 1
    
    # Now set a custom mock vector index
    class MockVI:
        def search(self, query, nodes, top_k):
            return nodes
    
    idx.set_vector_index(MockVI())
    res2 = await idx.route("database")
    assert len(res2) == 1
