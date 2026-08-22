"""Tests for KnowledgeExtractor.extract_from_text and IngestionWorker.

Covers the API integration and fallback paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.memory.extraction import KnowledgeExtractor
from vibe.memory.ingestion.worker import IngestionWorker
from vibe.memory.models import WikiPage


class FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


@pytest.mark.asyncio
async def test_extract_from_text_happy_path():
    # Setup mock LLM, Wiki, and PageIndex
    fake_llm = MagicMock()
    fake_llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=json.dumps(
                [
                    {
                        "title": "Clean Energy",
                        "content": "Clean energy comes from renewable zero-emission sources.",
                        "tags": ["energy", "environment"],
                        "citations": [],
                    }
                ]
            )
        )
    )

    fake_wiki = MagicMock()
    # Mock search_pages to return empty (page doesn't exist)
    fake_wiki.search_pages = AsyncMock(return_value=[])
    # Mock create_page to return a created WikiPage
    created_page = WikiPage(
        id="123",
        slug="clean-energy",
        title="Clean Energy",
        content="Clean energy comes from renewable zero-emission sources.",
        tags=["energy", "environment"],
        status="draft",
        date_created="2026-05-20",
        last_updated="2026-05-20",
        citations=[{"session": "test-doc"}],
        ttl_days=30,
        path=Path("/tmp/clean-energy.md"),
    )
    fake_wiki.create_page = AsyncMock(return_value=created_page)

    fake_pageindex = MagicMock()
    fake_pageindex.route = AsyncMock(return_value=[])
    root_node = MagicMock()
    root_node.node_id = "root"
    root_node.file_path = None
    root_node.sub_nodes = []
    fake_pageindex.load = MagicMock(return_value=root_node)
    fake_pageindex.add_node = MagicMock()

    extractor = KnowledgeExtractor(
        llm_client=fake_llm,
        wiki=fake_wiki,
        pageindex=fake_pageindex,
        config=None,
    )

    pages = await extractor.extract_from_text(
        text="Clean energy comes from renewable zero-emission sources.",
        source="test-doc",
        metadata={"chunk_index": 0},
    )

    assert len(pages) == 1
    assert pages[0].title == "Clean Energy"
    fake_wiki.create_page.assert_called_once()
    # Accepted pages are indexed via index_wiki_page() → PageIndex.add_node()
    fake_pageindex.add_node.assert_called_once_with(
        parent_id="root",
        title="Clean Energy",
        description="Wiki page: Clean Energy. Tags: energy, environment",
        file_path="/tmp/clean-energy.md",
        tags=["energy", "environment"],
    )


@pytest.mark.asyncio
async def test_extract_from_text_updates_existing():
    fake_llm = MagicMock()
    fake_llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=json.dumps(
                [
                    {
                        "title": "Clean Energy",
                        "content": "Updated content.",
                        "tags": ["energy"],
                        "citations": [],
                    }
                ]
            )
        )
    )

    fake_wiki = MagicMock()
    # Mock search_pages to return existing page
    existing_page = WikiPage(
        id="123",
        slug="clean-energy",
        title="Clean Energy",
        content="Clean energy comes from renewable zero-emission sources.",
        tags=["energy", "environment"],
        status="draft",
        date_created="2026-05-20",
        last_updated="2026-05-20",
        citations=[{"session": "test-doc"}],
        ttl_days=30,
        path=Path("/tmp/clean-energy.md"),
    )
    fake_wiki.search_pages = AsyncMock(return_value=[existing_page])
    fake_wiki.update_page = AsyncMock(return_value=existing_page)

    fake_pageindex = MagicMock()
    fake_pageindex.route = AsyncMock(return_value=[])
    root_node = MagicMock()
    root_node.node_id = "root"
    root_node.file_path = None
    root_node.sub_nodes = []
    fake_pageindex.load = MagicMock(return_value=root_node)
    fake_pageindex.add_node = MagicMock()

    extractor = KnowledgeExtractor(
        llm_client=fake_llm,
        wiki=fake_wiki,
        pageindex=fake_pageindex,
        config=None,
    )

    pages = await extractor.extract_from_text(
        text="Updated text.",
        source="test-doc",
    )

    assert len(pages) == 1
    fake_wiki.update_page.assert_called_once()
    fake_wiki.create_page.assert_not_called()


@pytest.mark.asyncio
async def test_ingestion_worker_with_extractor_api():
    # Setup worker and extractor that has extract_from_text
    extractor = MagicMock()
    extractor.extract_from_text = AsyncMock(return_value=[MagicMock()])

    worker = IngestionWorker(extractor=extractor)
    res = await worker._process_chunk(
        chunk="Test chunk",
        source_path=Path("doc.md"),
        index=2,
    )

    assert res == 1
    extractor.extract_from_text.assert_called_once_with(
        text="Test chunk",
        source="doc.md",
        metadata={"chunk_index": 2},
    )


@pytest.mark.asyncio
async def test_ingestion_worker_fallback():
    # Setup extractor with only wiki attribute (no extract_from_text)
    wiki = MagicMock()
    wiki.get_page_by_slug = AsyncMock(return_value=None)
    wiki.create_page = AsyncMock()

    extractor = MagicMock(spec=[])
    extractor.wiki = wiki

    worker = IngestionWorker(extractor=extractor)
    res = await worker._process_chunk(
        chunk="Test chunk",
        source_path=Path("doc.md"),
        index=2,
    )

    assert res == 1
    wiki.get_page_by_slug.assert_called_once_with("doc-chunk-2")
    wiki.create_page.assert_called_once_with(
        title="doc Chunk 2",
        content="Test chunk",
        tags=["document-chunk"],
        citations=[
            {
                "session": "document_ingestion",
                "source": "doc.md",
                "chunk_index": 2,
            }
        ],
        status="draft",
    )
