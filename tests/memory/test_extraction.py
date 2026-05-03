"""Unit tests for KnowledgeExtractor — Phase 1b Gated Auto-Extraction.

Covers: extract_from_session, score_novelty, apply_gates, error swallowing,
markdown code fence stripping, malformed JSON handling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.memory.extraction import KnowledgeExtractor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    role: str
    content: str


@dataclass
class FakeLLMResponse:
    content: str


@pytest.fixture
def fake_llm():
    """Return a mock LLM client that returns valid JSON."""
    client = MagicMock()
    client.complete = AsyncMock(return_value=FakeLLMResponse(
        content=json.dumps([
            {
                "title": "Docker Compose Network Mode",
                "content": "Docker Compose supports `network_mode: host`.",
                "tags": ["docker", "networking"],
                "citations": [{"session": "abc123", "message_index": 5}],
            }
        ])
    ))
    return client


@pytest.fixture
def fake_wiki():
    return MagicMock()


@pytest.fixture
def fake_pageindex():
    """Return a mock PageIndex that returns no similar pages."""
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    return idx


@pytest.fixture
def fake_flash_client():
    """Return a mock FlashLLMClient that scores confidence."""
    client = MagicMock()
    client.score_confidence = AsyncMock(return_value=0.95)
    return client


@pytest.fixture
def extractor(fake_llm, fake_wiki, fake_pageindex, fake_flash_client):
    return KnowledgeExtractor(
        llm_client=fake_llm,
        wiki=fake_wiki,
        pageindex=fake_pageindex,
        flash_client=fake_flash_client,
        config=None,
    )


# ---------------------------------------------------------------------------
# extract_from_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_from_session_valid_json(extractor):
    messages = [
        FakeMessage(role="user", content="How do I use Docker Compose?"),
        FakeMessage(role="assistant", content="Use network_mode: host."),
    ]
    items = await extractor.extract_from_session(messages, "sess-001")
    assert len(items) == 1
    assert items[0]["title"] == "Docker Compose Network Mode"
    assert items[0]["content"] == "Docker Compose supports `network_mode: host`."
    assert items[0]["tags"] == ["docker", "networking"]
    assert items[0]["citations"][0]["session"] == "abc123"


@pytest.mark.asyncio
async def test_extract_from_session_skips_system_and_tool(fake_llm, fake_wiki):
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [
        FakeMessage(role="system", content="You are a helpful assistant."),
        FakeMessage(role="tool", content="{'result': 'ok'}"),
        FakeMessage(role="user", content="Hello"),
    ]
    items = await extractor.extract_from_session(messages, "sess-002")
    # Should still succeed because user message is present
    assert len(items) == 1
    # Verify that system/tool messages were not included in transcript
    call_args = fake_llm.complete.await_args
    prompt = call_args[0][0]
    assert "You are a helpful assistant" not in prompt
    assert "'result': 'ok'" not in prompt


@pytest.mark.asyncio
async def test_extract_from_session_empty_transcript(fake_llm, fake_wiki):
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [
        FakeMessage(role="system", content="System prompt"),
        FakeMessage(role="tool", content="Tool result"),
    ]
    items = await extractor.extract_from_session(messages, "sess-003")
    assert items == []


@pytest.mark.asyncio
async def test_extract_from_session_malformed_json(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(return_value=FakeLLMResponse(content="not json at all"))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    items = await extractor.extract_from_session(messages, "sess-004")
    assert items == []


@pytest.mark.asyncio
async def test_extract_from_session_markdown_code_fences(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(return_value=FakeLLMResponse(
        content='```json\n[{"title": "Test", "content": "Content", "tags": ["t"]}]\n```'
    ))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    items = await extractor.extract_from_session(messages, "sess-005")
    assert len(items) == 1
    assert items[0]["title"] == "Test"


@pytest.mark.asyncio
async def test_extract_from_session_llm_returns_none(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(return_value=None)
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    items = await extractor.extract_from_session(messages, "sess-006")
    assert items == []


@pytest.mark.asyncio
async def test_extract_from_session_llm_raises(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    items = await extractor.extract_from_session(messages, "sess-007")
    assert items == []


@pytest.mark.asyncio
async def test_extract_from_session_not_a_list(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(return_value=FakeLLMResponse(
        content='{"title": "Not a list", "content": "Oops"}'
    ))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    items = await extractor.extract_from_session(messages, "sess-008")
    assert items == []


@pytest.mark.asyncio
async def test_extract_from_session_adds_default_citation(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(return_value=FakeLLMResponse(
        content=json.dumps([{"title": "No Citation", "content": "Text", "tags": []}])
    ))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    items = await extractor.extract_from_session(messages, "sess-009")
    assert len(items) == 1
    assert items[0]["citations"][0]["session"] == "sess-009"


# ---------------------------------------------------------------------------
# score_novelty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_novelty_all_novel_when_no_pageindex(extractor):
    extractor_no_idx = KnowledgeExtractor(
        llm_client=extractor.llm_client,
        wiki=extractor.wiki,
        pageindex=None,
    )
    items = [{"title": "A", "content": "B"}, {"title": "C", "content": "D"}]
    scores = await extractor_no_idx.score_novelty(items)
    assert scores == [1.0, 1.0]


@pytest.mark.asyncio
async def test_score_novelty_empty_items(extractor):
    scores = await extractor.score_novelty([])
    assert scores == []


@pytest.mark.asyncio
async def test_score_novelty_with_pageindex(fake_llm, fake_wiki):
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [{"title": "Unique Title", "content": "Unique content"}]
    scores = await extractor.score_novelty(items)
    assert scores == [1.0]


@pytest.mark.asyncio
async def test_score_novelty_exact_title_match(fake_llm, fake_wiki):
    node = MagicMock()
    node.title = "Exact Title"
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[node])
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [{"title": "Exact Title", "content": "Different content"}]
    scores = await extractor.score_novelty(items)
    assert scores == [0.0]


@pytest.mark.asyncio
async def test_score_novelty_near_duplicate(fake_llm, fake_wiki):
    node = MagicMock()
    node.title = "Word1 Word2 Word3 Word4 Word5"
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[node])
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [{"title": "Word1 Word2 Word3 Word4 Word5 Word6", "content": "X"}]
    scores = await extractor.score_novelty(items)
    assert scores == [0.1]


@pytest.mark.asyncio
async def test_score_novelty_exception_per_item(fake_llm, fake_wiki):
    idx = MagicMock()
    idx.route = AsyncMock(side_effect=RuntimeError("DB error"))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [{"title": "A", "content": "B"}]
    scores = await extractor.score_novelty(items)
    assert scores == [1.0]


# ---------------------------------------------------------------------------
# apply_gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_gates_novelty_filter(fake_llm, fake_wiki):
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [
        {"title": "A", "content": "Content A"},
        {"title": "B", "content": "Content B"},
    ]
    approved = await extractor.apply_gates(items, novelty_threshold=0.5, confidence_threshold=0.8)
    assert len(approved) == 2


@pytest.mark.asyncio
async def test_apply_gates_novelty_rejects_duplicates(fake_llm, fake_wiki):
    node = MagicMock()
    node.title = "Duplicate Title"
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[node])
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [{"title": "Duplicate Title", "content": "X"}]
    approved = await extractor.apply_gates(items, novelty_threshold=0.5, confidence_threshold=0.8)
    assert approved == []


@pytest.mark.asyncio
async def test_apply_gates_confidence_filter(fake_llm, fake_wiki, fake_flash_client):
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    fake_flash_client.score_confidence = AsyncMock(return_value=0.5)
    extractor = KnowledgeExtractor(
        llm_client=fake_llm, wiki=fake_wiki, pageindex=idx, flash_client=fake_flash_client
    )
    items = [{"title": "Low Confidence", "content": "X"}]
    approved = await extractor.apply_gates(items, novelty_threshold=0.5, confidence_threshold=0.8)
    assert approved == []


@pytest.mark.asyncio
async def test_apply_gates_confidence_pass(fake_llm, fake_wiki, fake_flash_client):
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    fake_flash_client.score_confidence = AsyncMock(return_value=0.95)
    extractor = KnowledgeExtractor(
        llm_client=fake_llm, wiki=fake_wiki, pageindex=idx, flash_client=fake_flash_client
    )
    items = [{"title": "High Confidence", "content": "X"}]
    approved = await extractor.apply_gates(items, novelty_threshold=0.5, confidence_threshold=0.8)
    assert len(approved) == 1
    assert approved[0]["_confidence"] == 0.95


@pytest.mark.asyncio
async def test_apply_gates_no_flash_client(fake_llm, fake_wiki):
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki, pageindex=idx)
    items = [{"title": "No Flash", "content": "X"}]
    approved = await extractor.apply_gates(items, novelty_threshold=0.5, confidence_threshold=0.8)
    assert len(approved) == 1


@pytest.mark.asyncio
async def test_apply_gates_flash_client_exception(fake_llm, fake_wiki, fake_flash_client):
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    fake_flash_client.score_confidence = AsyncMock(side_effect=RuntimeError("Flash error"))
    extractor = KnowledgeExtractor(
        llm_client=fake_llm, wiki=fake_wiki, pageindex=idx, flash_client=fake_flash_client
    )
    items = [{"title": "Flash Error", "content": "X"}]
    approved = await extractor.apply_gates(items, novelty_threshold=0.5, confidence_threshold=0.8)
    assert len(approved) == 1  # Pass through on error


@pytest.mark.asyncio
async def test_apply_gates_empty_items(extractor):
    approved = await extractor.apply_gates([])
    assert approved == []


# ---------------------------------------------------------------------------
# Error policy: never raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_from_session_never_raises(fake_llm, fake_wiki):
    fake_llm.complete = AsyncMock(side_effect=Exception("anything"))
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    messages = [FakeMessage(role="user", content="Hello")]
    # Should NOT raise
    items = await extractor.extract_from_session(messages, "sess-010")
    assert items == []


@pytest.mark.asyncio
async def test_apply_gates_never_raises(fake_llm, fake_wiki):
    extractor = KnowledgeExtractor(llm_client=fake_llm, wiki=fake_wiki)
    # Should NOT raise even with None items
    approved = await extractor.apply_gates(None)  # type: ignore[arg-type]
    assert approved == []
