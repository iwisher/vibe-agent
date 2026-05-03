"""Unit tests for PageIndex routing layer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from vibe.memory.models import IndexNode
from vibe.memory.pageindex import PageIndex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_index(tmp_path):
    return PageIndex(
        index_path=tmp_path / "index.json",
        llm_client=None,  # keyword routing only
    )


def make_page_node(node_id: str, title: str, tags: list[str], file_path: str = "/wiki/p.md") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "description": f"Page about {title}",
        "file_path": file_path,
        "tags": tags,
        "sub_nodes": [],
    }


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------


def test_load_empty_index(tmp_index):
    root = tmp_index.load()
    assert root.node_id == "root_01"
    assert root.sub_nodes == []


def test_save_and_reload(tmp_path):
    idx = PageIndex(index_path=tmp_path / "idx.json")
    idx.load()
    idx.add_node("root_01", "Test Node", "A test page", tags=["test"])
    idx._save()

    # New instance should load saved state
    idx2 = PageIndex(index_path=tmp_path / "idx.json")
    root = idx2.load()
    assert len(root.sub_nodes) == 1
    assert root.sub_nodes[0].title == "Test Node"


def test_index_json_schema(tmp_path):
    """index.json must have wiki_index root key."""
    idx = PageIndex(index_path=tmp_path / "idx.json")
    idx.load()
    idx.add_node("root_01", "Node A", "Description A", tags=["a"])
    idx._save()

    data = json.loads((tmp_path / "idx.json").read_text())
    assert "wiki_index" in data
    assert data["wiki_index"]["node_id"] == "root_01"


# ---------------------------------------------------------------------------
# Node management
# ---------------------------------------------------------------------------


def test_add_node(tmp_index):
    tmp_index.load()
    node = tmp_index.add_node("root_01", "Database Logs", "DB scaling info", tags=["database"])
    assert node.title == "Database Logs"
    assert node.node_id.startswith("doc_")
    assert "database" in node.tags


def test_add_node_to_nonexistent_parent_falls_back_to_root(tmp_index):
    tmp_index.load()
    node = tmp_index.add_node("nonexistent_parent", "Orphan", "Desc", tags=[])
    # Should still be added (under root as fallback)
    assert node.title == "Orphan"


def test_remove_node(tmp_index):
    tmp_index.load()
    node = tmp_index.add_node("root_01", "Remove Me", "Desc", tags=[])
    assert tmp_index._find_node(tmp_index._root, node.node_id) is not None

    result = tmp_index.remove_node(node.node_id)
    assert result is True
    assert tmp_index._find_node(tmp_index._root, node.node_id) is None


def test_remove_nonexistent_node(tmp_index):
    tmp_index.load()
    result = tmp_index.remove_node("does_not_exist")
    assert result is False


def test_update_node(tmp_index):
    tmp_index.load()
    node = tmp_index.add_node("root_01", "Original", "Desc", tags=["old"])
    tmp_index.update_node(node.node_id, title="Updated Title", tags=["new"])
    updated = tmp_index._find_node(tmp_index._root, node.node_id)
    assert updated.title == "Updated Title"
    assert updated.tags == ["new"]


# ---------------------------------------------------------------------------
# Routing (keyword fallback — no LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_returns_empty_when_no_nodes(tmp_index):
    tmp_index.load()
    results = await tmp_index.route("database scaling")
    assert results == []


@pytest.mark.asyncio
async def test_route_keyword_match(tmp_path):
    idx = PageIndex(index_path=tmp_path / "idx.json", llm_client=None)
    idx.load()
    idx.add_node("root_01", "Database Scaling", "DB performance info", tags=["database"], file_path="/wiki/db.md")
    idx.add_node("root_01", "UI Design", "User interface patterns", tags=["ui"], file_path="/wiki/ui.md")

    results = await idx.route("database performance")
    titles = [n.title for n in results]
    assert "Database Scaling" in titles
    assert "UI Design" not in titles


@pytest.mark.asyncio
async def test_route_returns_confidence_scores(tmp_path):
    idx = PageIndex(index_path=tmp_path / "idx.json", llm_client=None)
    idx.load()
    idx.add_node("root_01", "Scaling Docs", "Database scaling documentation", tags=["database", "scaling"], file_path="/wiki/s.md")

    results = await idx.route("database scaling issues")
    assert len(results) > 0
    for node in results:
        assert 0.0 <= node.confidence <= 1.0


@pytest.mark.asyncio
async def test_route_no_match_returns_empty(tmp_path):
    idx = PageIndex(index_path=tmp_path / "idx.json", llm_client=None)
    idx.load()
    idx.add_node("root_01", "Database Page", "DB stuff", tags=["database"], file_path="/wiki/db.md")

    results = await idx.route("zxqjvm xylophone")
    assert results == []


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def test_partition_triggers_at_node_threshold(tmp_path):
    """Partitioning should trigger when node count exceeds max_nodes_per_index."""
    idx = PageIndex(
        index_path=tmp_path / "idx.json",
        max_nodes_per_index=5,
        token_threshold=999999,  # High threshold to not trigger on tokens
    )
    idx.load()

    # Add 6 leaf nodes (above threshold of 5)
    for i in range(6):
        idx.add_node("root_01", f"Node {i}", f"Page {i}", tags=[f"tag{i % 3}"], file_path=f"/wiki/p{i}.md")

    # Should have triggered partitioning — root sub_nodes should now contain categories
    root = idx._root
    # At least some category nodes should exist
    category_nodes = [n for n in root.sub_nodes if not n.file_path]
    assert len(category_nodes) > 0, "Expected category nodes after partitioning"


def test_partition_tag_bucketing_deterministic(tmp_path):
    """Same input should always produce same partition result."""
    def build_idx(path):
        idx = PageIndex(
            index_path=path / "idx.json",
            max_nodes_per_index=3,
        )
        idx.load()
        idx.add_node("root_01", "Alpha Page", "Desc", tags=["alpha"], file_path="/wiki/a.md")
        idx.add_node("root_01", "Beta Page", "Desc", tags=["beta"], file_path="/wiki/b.md")
        idx.add_node("root_01", "Gamma Page", "Desc", tags=["alpha"], file_path="/wiki/c.md")
        idx.add_node("root_01", "Delta Page", "Desc", tags=["beta"], file_path="/wiki/d.md")
        return idx

    # Use separate directories to avoid state contamination
    run1_path = tmp_path / "run1"
    run2_path = tmp_path / "run2"
    run1_path.mkdir()
    run2_path.mkdir()

    idx1 = build_idx(run1_path)
    idx2 = build_idx(run2_path)

    cats1 = sorted(n.node_id for n in idx1._root.sub_nodes if not n.file_path)
    cats2 = sorted(n.node_id for n in idx2._root.sub_nodes if not n.file_path)
    assert cats1 == cats2, "Partitioning category IDs must be deterministic"
    # Also verify same category titles
    titles1 = sorted(n.title for n in idx1._root.sub_nodes if not n.file_path)
    titles2 = sorted(n.title for n in idx2._root.sub_nodes if not n.file_path)
    assert titles1 == titles2, "Partitioning category titles must be deterministic"




# ---------------------------------------------------------------------------
# Paraphrase routing (vector index)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_paraphrase_query(tmp_path):
    """Paraphrase queries should match semantically similar pages."""
    idx = PageIndex(index_path=tmp_path / "idx.json", llm_client=None)
    idx.load()
    idx.add_node(
        "root_01",
        "Rust Programming Guide",
        "Learn how to write Rust code",
        tags=["rust", "programming"],
        file_path="/wiki/rust.md",
    )

    # Mock vector index that returns nodes for paraphrase queries
    class ParaphraseVI:
        def search(self, query, nodes, top_k):
            # Match paraphrases of rust programming
            rust_terms = {"rust", "programming", "write", "guide", "how to"}
            query_words = set(query.lower().split())
            if query_words & rust_terms:
                return [
                    IndexNode(
                        node_id=n.node_id,
                        title=n.title,
                        description=n.description,
                        file_path=n.file_path,
                        tags=n.tags,
                        confidence=0.85,
                    )
                    for n in nodes
                ]
            return []

    idx.set_vector_index(ParaphraseVI())
    results = await idx.route("how to write rust")
    assert len(results) == 1
    assert results[0].title == "Rust Programming Guide"


# ---------------------------------------------------------------------------
# IndexNode serialization
# ---------------------------------------------------------------------------


def test_index_node_to_from_dict():
    node = IndexNode(
        node_id="test_01",
        title="Test",
        description="A test node",
        file_path="/wiki/test.md",
        tags=["a", "b"],
    )
    d = node.to_dict()
    restored = IndexNode.from_dict(d)
    assert restored.node_id == node.node_id
    assert restored.title == node.title
    assert restored.tags == node.tags
    assert restored.file_path == node.file_path


def test_index_node_sub_index_path():
    node = IndexNode(
        node_id="cat_01",
        title="Category",
        description="A category node",
        sub_index_path="index_dev.json",
        tags=["dev"],
    )
    d = node.to_dict()
    assert d["sub_index_path"] == "index_dev.json"
    restored = IndexNode.from_dict(d)
    assert restored.sub_index_path == "index_dev.json"
