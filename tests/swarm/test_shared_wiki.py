"""Tests for SharedWiki read-only wrapper and update requests."""

import pytest

from vibe.swarm.shared_wiki import SharedWiki, WikiUpdateRequest


class FakeWiki:
    """Fake wiki backend for testing."""

    def __init__(self):
        self.pages = {
            "python": {"slug": "python", "title": "Python", "content": "A language"},
            "async": {"slug": "async", "title": "AsyncIO", "content": "Concurrency"},
        }

    async def get_page(self, slug: str):
        return self.pages.get(slug)

    async def search(self, query: str, limit: int = 10):
        return [
            {"slug": p["slug"], "title": p["title"]}
            for p in self.pages.values()
            if query.lower() in p["title"].lower()
        ]

    async def list_pages(self, tag=None, status=None):
        pages = list(self.pages.values())
        if tag:
            pages = [p for p in pages if tag in p.get("tags", [])]
        return pages

    async def get_graph(self):
        return {"nodes": [{"id": "n1"}], "edges": []}


class TestSharedWiki:
    @pytest.mark.asyncio
    async def test_get_page(self):
        wiki = SharedWiki(wiki_backend=FakeWiki())
        page = await wiki.get_page("python")
        assert page is not None
        assert page["title"] == "Python"

    @pytest.mark.asyncio
    async def test_get_page_not_found(self):
        wiki = SharedWiki(wiki_backend=FakeWiki())
        page = await wiki.get_page("nonexistent")
        assert page is None

    @pytest.mark.asyncio
    async def test_search(self):
        wiki = SharedWiki(wiki_backend=FakeWiki())
        results = await wiki.search("Python")
        assert len(results) == 1
        assert results[0]["slug"] == "python"

    @pytest.mark.asyncio
    async def test_list_pages(self):
        wiki = SharedWiki(wiki_backend=FakeWiki())
        pages = await wiki.list_pages()
        assert len(pages) == 2

    @pytest.mark.asyncio
    async def test_get_graph(self):
        wiki = SharedWiki(wiki_backend=FakeWiki())
        graph = await wiki.get_graph()
        assert len(graph["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_no_backend(self):
        wiki = SharedWiki()
        assert await wiki.get_page("x") is None
        assert await wiki.search("x") == []
        assert await wiki.list_pages() == []
        assert await wiki.get_graph() == {"nodes": [], "edges": []}

    def test_wiki_update_request(self):
        req = WikiUpdateRequest(
            agent_id="research-1",
            page_slug="new-topic",
            title="New Topic",
            content="Some content",
            tags=["ai", "ml"],
            reason="Extracted from conversation",
        )
        assert req.agent_id == "research-1"
        assert req.page_slug == "new-topic"
        assert req.tags == ["ai", "ml"]

    def test_request_update_noop(self):
        wiki = SharedWiki()
        req = WikiUpdateRequest(
            agent_id="test",
            page_slug="x",
            title="X",
            content="Y",
            tags=[],
            reason="test",
        )
        # Should not raise
        wiki.request_update(req)
