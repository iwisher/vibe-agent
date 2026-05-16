"""Tests for wiki graph database and entity resolution."""

import tempfile

import pytest

from vibe.memory.wiki_graph import EntityEdge, EntityNode, WikiGraph


class TestWikiGraph:
    @pytest.fixture
    def graph(self):
        return WikiGraph()

    def test_add_entity(self, graph):
        entity = EntityNode(
            entity_id="e1",
            name="Python",
            aliases=["py", "python-lang"],
            page_ids=["page_1"],
        )
        graph.add_entity(entity)
        assert graph.entity_count == 1
        assert graph.resolve_entity("py") is not None

    def test_resolve_entity_by_alias(self, graph):
        entity = EntityNode(
            entity_id="e1",
            name="Python",
            aliases=["py"],
        )
        graph.add_entity(entity)
        resolved = graph.resolve_entity("py")
        assert resolved is not None
        assert resolved.name == "Python"

    def test_resolve_entity_not_found(self, graph):
        assert graph.resolve_entity("nonexistent") is None

    def test_add_edge(self, graph):
        e1 = EntityNode(entity_id="e1", name="Python")
        e2 = EntityNode(entity_id="e2", name="Django")
        graph.add_entity(e1)
        graph.add_entity(e2)
        graph.add_edge(EntityEdge("e1", "e2", "framework_for", weight=0.9))
        assert graph.edge_count == 1

    def test_add_edge_missing_entity(self, graph):
        graph.add_edge(EntityEdge("e1", "e2", "relation"))
        assert graph.edge_count == 0  # Should not add

    def test_get_related(self, graph):
        e1 = EntityNode(entity_id="e1", name="Python")
        e2 = EntityNode(entity_id="e2", name="Django")
        e3 = EntityNode(entity_id="e3", name="Flask")
        graph.add_entity(e1)
        graph.add_entity(e2)
        graph.add_entity(e3)
        graph.add_edge(EntityEdge("e1", "e2", "framework_for", weight=0.9))
        graph.add_edge(EntityEdge("e1", "e3", "framework_for", weight=0.7))

        related = graph.get_related("e1")
        assert len(related) == 2
        assert related[0][0].name == "Django"  # Higher weight first

    def test_get_related_filtered(self, graph):
        e1 = EntityNode(entity_id="e1", name="Python")
        e2 = EntityNode(entity_id="e2", name="Django")
        graph.add_entity(e1)
        graph.add_entity(e2)
        graph.add_edge(EntityEdge("e1", "e2", "framework_for"))
        graph.add_edge(EntityEdge("e1", "e2", "unrelated"))

        related = graph.get_related("e1", relation="framework_for")
        assert len(related) == 1

    def test_merge_entities(self, graph):
        e1 = EntityNode(entity_id="e1", name="Python", aliases=["py"], page_ids=["p1"])
        e2 = EntityNode(entity_id="e2", name="Python Language", aliases=["python"], page_ids=["p2"])
        graph.add_entity(e1)
        graph.add_entity(e2)
        graph.add_edge(EntityEdge("e1", "e2", "same_as"))

        assert graph.merge_entities("e1", "e2") is True
        assert graph.entity_count == 1
        merged = graph._entities["e1"]
        assert "python" in merged.aliases
        assert "p2" in merged.page_ids

    def test_merge_entities_not_found(self, graph):
        assert graph.merge_entities("e1", "e2") is False

    def test_find_entity_for_page(self, graph):
        e1 = EntityNode(entity_id="e1", name="Python", page_ids=["page_1"])
        graph.add_entity(e1)
        found = graph.find_entity_for_page("page_1")
        assert found is not None
        assert found.name == "Python"

    def test_save_and_load(self, graph):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        graph = WikiGraph(graph_path=path)
        e1 = EntityNode(entity_id="e1", name="Python", aliases=["py"])
        e2 = EntityNode(entity_id="e2", name="Django")
        graph.add_entity(e1)
        graph.add_entity(e2)
        graph.add_edge(EntityEdge("e1", "e2", "framework_for"))
        graph.save()

        # Load into new graph
        graph2 = WikiGraph(graph_path=path)
        graph2.load()
        assert graph2.entity_count == 2
        assert graph2.edge_count == 1
        assert graph2.resolve_entity("py") is not None

    def test_list_entities_by_type(self, graph):
        graph.add_entity(EntityNode(entity_id="e1", name="Python", entity_type="language"))
        graph.add_entity(EntityNode(entity_id="e2", name="Django", entity_type="framework"))
        languages = graph.list_entities(entity_type="language")
        assert len(languages) == 1
        assert languages[0].name == "Python"

    def test_suggest_merge_candidates(self, graph):
        graph.add_entity(EntityNode(entity_id="e1", name="Python", aliases=["py"]))
        graph.add_entity(EntityNode(entity_id="e2", name="Python", aliases=["python"]))
        graph.add_entity(EntityNode(entity_id="e3", name="Java"))

        candidates = graph.suggest_merge_candidates(similarity_threshold=0.5)
        assert len(candidates) == 1
        assert candidates[0][0] in ("e1", "e2")
        assert candidates[0][1] in ("e1", "e2")
