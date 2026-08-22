"""Unit tests for _wiki_extract_task integration in QueryLoop.

Covers: auto-extraction spawn conditions, non-blocking behavior,
extraction error handling, close() cancellation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.query_loop import QueryLoop

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMResponse:
    content: str = ""
    is_error: bool = False
    error: str = ""
    tool_calls: list | None = None
    usage: dict | None = None
    finish_reason: str | None = None
    model_used: str | None = None
    reasoning_content: str | None = None


@pytest.fixture
def fake_llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=FakeLLMResponse(content="Done"))
    client.model = "test-model"

    async def _stream(*args, **kwargs):
        yield FakeLLMResponse(content="Done", finish_reason="stop")

    # Use a MagicMock with a side_effect so query_loop's Mock detection works
    stream_mock = MagicMock()
    stream_mock.side_effect = _stream
    client.complete_stream = stream_mock
    return client


@pytest.fixture
def fake_tools():
    ts = MagicMock()
    ts.get_tool_schemas = MagicMock(return_value=[])
    return ts


@pytest.fixture
def fake_wiki():
    wiki = MagicMock()
    wiki.create_page = AsyncMock()
    wiki.update_page = AsyncMock()
    wiki.search = AsyncMock(return_value=[])
    return wiki


@pytest.fixture
def fake_pageindex():
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    return idx


@pytest.fixture
def fake_telemetry():
    tel = MagicMock()
    tel.record_session = MagicMock()
    return tel


@pytest.fixture
def fake_config():
    cfg = MagicMock()
    # QueryLoop reads _config_memory from config.tripartite, not config.wiki
    cfg.tripartite.wiki.auto_extract = True
    cfg.tripartite.wiki.novelty_threshold = 0.5
    cfg.tripartite.wiki.confidence_threshold = 0.8
    cfg.tripartite.rlm.enabled = False
    cfg.query_loop = None
    cfg.retry = None
    return cfg


@pytest.fixture
def query_loop(fake_llm, fake_tools, fake_wiki, fake_pageindex, fake_telemetry, fake_config):
    return QueryLoop(
        llm_client=fake_llm,
        tool_system=fake_tools,
        wiki=fake_wiki,
        pageindex=fake_pageindex,
        telemetry=fake_telemetry,
        config=fake_config,
        max_iterations=1,
    )


# ---------------------------------------------------------------------------
# Auto-extraction spawn conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_extract_spawns_when_enabled(query_loop, fake_config):
    fake_config.wiki.auto_extract = True
    query_loop.add_user_message("Hello")
    # Consume the generator
    async for _ in query_loop.run():
        pass
    # After run completes, _wiki_extract_task should be set
    assert query_loop._wiki_extract_task is not None
    # Wait for background task to finish
    if query_loop._wiki_extract_task and not query_loop._wiki_extract_task.done():
        try:
            await asyncio.wait_for(query_loop._wiki_extract_task, timeout=2.0)
        except asyncio.TimeoutError:
            query_loop._wiki_extract_task.cancel()


@pytest.mark.asyncio
async def test_auto_extract_does_not_spawn_when_disabled(fake_llm, fake_tools, fake_config):
    fake_config.wiki.auto_extract = False
    ql = QueryLoop(
        llm_client=fake_llm,
        tool_system=fake_tools,
        config=fake_config,
        max_iterations=1,
    )
    ql.add_user_message("Hello")
    async for _ in ql.run():
        pass
    assert ql._wiki_extract_task is None


@pytest.mark.asyncio
async def test_auto_extract_does_not_spawn_without_wiki(fake_llm, fake_tools, fake_config):
    fake_config.wiki.auto_extract = True
    ql = QueryLoop(
        llm_client=fake_llm,
        tool_system=fake_tools,
        wiki=None,
        config=fake_config,
        max_iterations=1,
    )
    ql.add_user_message("Hello")
    async for _ in ql.run():
        pass
    assert ql._wiki_extract_task is None


# ---------------------------------------------------------------------------
# Non-blocking behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_does_not_block_response(query_loop):
    """User response should be yielded before extraction completes."""
    query_loop.add_user_message("Hello")
    results = []
    async for result in query_loop.run():
        results.append(result)
    # Should get at least one result (the assistant response)
    assert len(results) >= 1
    # Extraction task may still be running
    if query_loop._wiki_extract_task and not query_loop._wiki_extract_task.done():
        query_loop._wiki_extract_task.cancel()
        try:
            await query_loop._wiki_extract_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_errors_caught_and_logged(query_loop, fake_wiki, fake_config):
    fake_config.wiki.auto_extract = True
    # Make wiki.create_page raise to simulate extraction failure
    fake_wiki.create_page = AsyncMock(side_effect=RuntimeError("Wiki write failed"))
    query_loop.add_user_message("Hello")
    # Should NOT raise — errors are swallowed
    async for _ in query_loop.run():
        pass
    # Wait for extraction task
    if query_loop._wiki_extract_task and not query_loop._wiki_extract_task.done():
        try:
            await asyncio.wait_for(query_loop._wiki_extract_task, timeout=2.0)
        except asyncio.TimeoutError:
            query_loop._wiki_extract_task.cancel()


# ---------------------------------------------------------------------------
# close() cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_cancels_pending_extraction_task(query_loop, fake_config):
    fake_config.wiki.auto_extract = True
    query_loop.add_user_message("Hello")
    async for _ in query_loop.run():
        pass
    # Ensure task exists and may still be running
    assert query_loop._wiki_extract_task is not None
    # close() should cancel it without error
    await query_loop.close()
    # After close, task should be done or cancelled
    if query_loop._wiki_extract_task:
        assert query_loop._wiki_extract_task.done()


@pytest.mark.asyncio
async def test_close_cancels_pending_rlm_task(fake_llm, fake_tools, fake_config):
    fake_config.tripartite.wiki.auto_extract = False
    fake_config.tripartite.rlm.enabled = True
    fake_telemetry = MagicMock()
    fake_telemetry.record_session = MagicMock()
    ql = QueryLoop(
        llm_client=fake_llm,
        tool_system=fake_tools,
        telemetry=fake_telemetry,
        config=fake_config,
        max_iterations=1,
    )
    ql.add_user_message("Hello")
    async for _ in ql.run():
        pass
    assert ql._rlm_trigger_task is not None
    await ql.close()
    if ql._rlm_trigger_task:
        assert ql._rlm_trigger_task.done()


# ---------------------------------------------------------------------------
# _build_wiki_hint: confidence gate, injectable filter, bounded snippets
# ---------------------------------------------------------------------------


class _Node:
    """Minimal PageIndex node stand-in for _build_wiki_hint tests."""

    def __init__(self, confidence: float, file_path: str):
        self.confidence = confidence
        self.file_path = file_path


@pytest.fixture
def real_wiki(tmp_path):
    from vibe.memory.wiki import LLMWiki

    return LLMWiki(base_path=tmp_path / "wiki")


def _hint_loop(fake_llm, fake_tools, nodes):
    """QueryLoop with a fake pageindex that routes to the given nodes."""
    idx = MagicMock()
    idx.route = AsyncMock(return_value=nodes)
    return QueryLoop(llm_client=fake_llm, tool_system=fake_tools, pageindex=idx, max_iterations=1)


@pytest.mark.asyncio
async def test_wiki_hint_confidence_gate_drops_low_confidence(fake_llm, fake_tools, real_wiki):
    high = await real_wiki.create_page(
        title="High Confidence Page", content="Solid fact about python venvs.", tags=["python"]
    )
    low = await real_wiki.create_page(
        title="Low Confidence Page", content="Shaky claim about python venvs.", tags=["python"]
    )
    loop = _hint_loop(
        fake_llm,
        fake_tools,
        [_Node(0.9, str(high.path)), _Node(0.1, str(low.path))],
    )
    hint = await loop._build_wiki_hint("python venv", min_confidence=0.3)
    assert "High Confidence Page" in hint
    assert "Low Confidence Page" not in hint
    await real_wiki.close()


@pytest.mark.asyncio
async def test_wiki_hint_excludes_contradicted_page(fake_llm, fake_tools, real_wiki):
    page = await real_wiki.create_page(
        title="Contradicted Page",
        content="Conflicting claim.",
        tags=["x"],
        citations=[{"type": "contradiction_flag", "session": "s1"}],
    )
    ok = await real_wiki.create_page(
        title="Clean Page", content="Non-conflicting fact.", tags=["x"]
    )
    loop = _hint_loop(
        fake_llm,
        fake_tools,
        [_Node(0.9, str(page.path)), _Node(0.9, str(ok.path))],
    )
    hint = await loop._build_wiki_hint("x", min_confidence=0.3)
    assert "Contradicted Page" not in hint
    assert "Conflicting claim." not in hint
    assert "Clean Page" in hint
    await real_wiki.close()


@pytest.mark.asyncio
async def test_wiki_hint_excludes_expired_page(fake_llm, fake_tools, real_wiki):
    expired = await real_wiki.create_page(
        title="Expired Page", content="Stale knowledge.", tags=["x"], status="expired"
    )
    loop = _hint_loop(fake_llm, fake_tools, [_Node(0.9, str(expired.path))])
    hint = await loop._build_wiki_hint("x", min_confidence=0.3)
    assert hint == ""
    await real_wiki.close()


@pytest.mark.asyncio
async def test_wiki_hint_snippet_content_bounded(fake_llm, fake_tools, real_wiki):
    long_content = "lorem ipsum " * 200  # ~2400 chars
    page = await real_wiki.create_page(title="Long Page", content=long_content, tags=["verbose"])
    loop = _hint_loop(fake_llm, fake_tools, [_Node(0.9, str(page.path))])
    hint = await loop._build_wiki_hint("lorem", min_confidence=0.3)
    assert "Long Page" in hint
    # Snippet capped at 500 chars of content
    assert long_content not in hint
    assert len(hint) < 700
    await real_wiki.close()


@pytest.mark.asyncio
async def test_wiki_hint_never_raises(fake_llm, fake_tools):
    idx = MagicMock()
    idx.route = AsyncMock(side_effect=RuntimeError("index exploded"))
    loop = QueryLoop(llm_client=fake_llm, tool_system=fake_tools, pageindex=idx, max_iterations=1)
    assert await loop._build_wiki_hint("anything") == ""


# ---------------------------------------------------------------------------
# ERROR-state extraction trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_state_triggers_extraction(fake_llm, fake_tools, fake_wiki, fake_config):
    """ERROR sessions carry the most valuable lessons — extraction must still run."""
    fake_config.memory.wiki.auto_extract = True
    fake_config.memory.wiki.novelty_threshold = 0.5
    fake_config.memory.wiki.confidence_threshold = 0.8
    fake_config.memory.rlm.enabled = False
    # Drive the loop into ERROR state
    fake_llm.complete = AsyncMock(
        return_value=FakeLLMResponse(content="", is_error=True, error="boom")
    )
    idx = MagicMock()
    idx.route = AsyncMock(return_value=[])
    ql = QueryLoop(
        llm_client=fake_llm,
        tool_system=fake_tools,
        wiki=fake_wiki,
        pageindex=idx,
        config=fake_config,
        max_iterations=1,
        stream=False,  # force the non-streaming path so complete() is used
    )
    ql.add_user_message("Hello")
    async for _ in ql.run():
        pass
    assert ql._state.name == "ERROR"
    assert ql._wiki_extract_task is not None
    if not ql._wiki_extract_task.done():
        try:
            await asyncio.wait_for(ql._wiki_extract_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            ql._wiki_extract_task.cancel()
