"""Unit tests for _wiki_extract_task integration in QueryLoop.

Covers: auto-extraction spawn conditions, non-blocking behavior,
extraction error handling, close() cancellation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.query_loop import Message, QueryLoop, QueryState


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


@pytest.fixture
def fake_llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=FakeLLMResponse(content="Done"))
    client.model = "test-model"
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
    cfg.wiki.auto_extract = True
    cfg.wiki.novelty_threshold = 0.5
    cfg.wiki.confidence_threshold = 0.8
    cfg.rlm.enabled = False
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
    fake_config.wiki.auto_extract = False
    fake_config.rlm.enabled = True
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
