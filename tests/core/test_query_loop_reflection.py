"""Unit tests for trajectory reflection hook in QueryLoop.

Covers: reflection task spawn conditions (COMPLETED / ERROR / disabled),
non-blocking + never-raises behavior, close() awaiting the reflection task,
and tool-name metadata on tool Messages.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.config import ReflectionConfig, TripartiteMemoryConfig, WikiConfig
from vibe.core.query_loop import QueryLoop
from vibe.memory.pageindex import PageIndex
from vibe.memory.wiki import LLMWiki

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


_LESSON_JSON = json.dumps(
    [
        {
            "title": "Pin base image digests",
            "lesson": "When pulling base images, pin by digest because tags drift. " * 3,
            "applies_when": "Writing Dockerfiles",
            "kind": "procedure",
        }
    ]
)

_PITFALL_JSON = json.dumps(
    [
        {
            "title": "Do not force-push shared branches",
            "lesson": "When push is rejected, pull --rebase instead of force-pushing. " * 3,
            "applies_when": "git push rejected",
            "kind": "pitfall",
        }
    ]
)


def _make_llm(*contents: str):
    """LLM mock whose complete() returns the given contents in order."""
    client = MagicMock()
    client.complete = AsyncMock(side_effect=[FakeLLMResponse(content=c) for c in contents])
    client.model = "test-model"
    return client


@pytest.fixture
def fake_tools():
    ts = MagicMock()
    ts.get_tool_schemas = MagicMock(return_value=[])
    return ts


@pytest.fixture
def wiki(tmp_path):
    return LLMWiki(base_path=tmp_path / "wiki")


@pytest.fixture
def pageindex(tmp_path):
    return PageIndex(index_path=tmp_path / "index.json")


def _config(reflection_enabled: bool = True, memory_enabled: bool = True):
    # auto_extract disabled so the extraction task does not race reflection
    # for the mocked LLM's side_effect queue — extraction has its own tests.
    mem = TripartiteMemoryConfig(
        enabled=memory_enabled,
        wiki=WikiConfig(auto_extract=False),
        reflection=ReflectionConfig(enabled=reflection_enabled, min_transcript_chars=10),
    )
    return SimpleNamespace(memory=mem, query_loop=None, retry=None)


async def _drain(loop: QueryLoop) -> None:
    async for _ in loop.run():
        pass


async def _await_task(task, timeout: float = 2.0) -> None:
    if task is not None and not task.done():
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


# ---------------------------------------------------------------------------
# Spawn conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflection_spawns_on_completed(fake_tools, wiki, pageindex):
    llm = _make_llm("Here is a detailed answer about dockerizing apps. " * 10, _LESSON_JSON)
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=wiki,
        pageindex=pageindex,
        config=_config(),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("How do I dockerize my Python web application with compose? " * 4)
    await _drain(loop)
    assert loop._reflection_task is not None
    await _await_task(loop._reflection_task)
    assert loop._reflection_task.done()

    pages = await wiki.list_pages(tag="lesson")
    assert len(pages) == 1
    assert pages[0].title == "Pin base image digests"
    assert any(c.get("session") == loop._session_id for c in pages[0].citations)


@pytest.mark.asyncio
async def test_reflection_spawns_on_error(fake_tools, wiki, pageindex):
    llm = MagicMock()
    llm.complete = AsyncMock(
        side_effect=[
            FakeLLMResponse(content="", is_error=True, error="boom"),
            FakeLLMResponse(content=_PITFALL_JSON),
        ]
    )
    llm.model = "test-model"
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=wiki,
        pageindex=pageindex,
        config=_config(),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("Push my branch to origin please, it is urgent. " * 4)
    await _drain(loop)
    assert loop._state.name == "ERROR"
    assert loop._reflection_task is not None
    await _await_task(loop._reflection_task)

    pages = await wiki.list_pages(tag="lesson")
    assert len(pages) == 1
    assert "pitfall" in pages[0].tags
    assert "harmful: 1" in pages[0].content


@pytest.mark.asyncio
async def test_reflection_not_spawned_when_disabled(fake_tools, wiki, pageindex):
    llm = _make_llm("A long enough answer to pass the skip heuristic. " * 10)
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=wiki,
        pageindex=pageindex,
        config=_config(reflection_enabled=False),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("Tell me something interesting about python async. " * 4)
    await _drain(loop)
    assert loop._reflection_task is None


@pytest.mark.asyncio
async def test_reflection_not_spawned_when_memory_disabled(fake_tools, wiki, pageindex):
    llm = _make_llm("A long enough answer to pass the skip heuristic. " * 10)
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=wiki,
        pageindex=pageindex,
        config=_config(memory_enabled=False),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("Tell me something interesting about python async. " * 4)
    await _drain(loop)
    assert loop._reflection_task is None


@pytest.mark.asyncio
async def test_reflection_not_spawned_without_wiki(fake_tools, pageindex):
    llm = _make_llm("A long enough answer to pass the skip heuristic. " * 10)
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=None,
        pageindex=pageindex,
        config=_config(),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("Tell me something interesting about python async. " * 4)
    await _drain(loop)
    assert loop._reflection_task is None


@pytest.mark.asyncio
async def test_reflection_never_breaks_run(fake_tools, wiki, pageindex):
    """A reflection LLM that explodes must not affect the user-facing run."""
    llm = _make_llm("A long enough answer to pass the skip heuristic. " * 10)
    # Reflection call (second complete()) raises
    llm.complete = AsyncMock(
        side_effect=[
            FakeLLMResponse(content="A long enough answer to pass the skip heuristic. " * 10),
            RuntimeError("reflection exploded"),
        ]
    )
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=wiki,
        pageindex=pageindex,
        config=_config(),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("Tell me something interesting about python async. " * 4)
    results = [r async for r in loop.run() if not r.is_status]
    assert loop._state.name == "COMPLETED"
    assert results[0].error is None
    assert "long enough answer" in results[0].response
    await _await_task(loop._reflection_task)
    # No lesson written, nothing raised
    assert await wiki.list_pages() == []


# ---------------------------------------------------------------------------
# Lifecycle: close() settles the reflection task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_awaits_reflection_task(fake_tools, wiki, pageindex):
    llm = _make_llm("A long enough answer to pass the skip heuristic. " * 10, _LESSON_JSON)
    loop = QueryLoop(
        llm_client=llm,
        tool_system=fake_tools,
        wiki=wiki,
        pageindex=pageindex,
        config=_config(),
        max_iterations=1,
        stream=False,
    )
    loop.add_user_message("How do I dockerize my Python web application with compose? " * 4)
    await _drain(loop)
    assert loop._reflection_task is not None
    # close() must await the task to completion (not cancel it)
    await loop.close()
    assert loop._reflection_task.done()
    assert not loop._reflection_task.cancelled()
    pages = await wiki.list_pages(tag="lesson")
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_close_cancels_overrunning_task_after_grace(fake_tools):
    llm = _make_llm()
    loop = QueryLoop(llm_client=llm, tool_system=fake_tools, max_iterations=1)
    loop._close_task_grace_seconds = 0.05

    async def _hanging():
        await asyncio.sleep(100)

    loop._reflection_task = asyncio.create_task(_hanging())
    await loop.close()
    assert loop._reflection_task.cancelled()


# ---------------------------------------------------------------------------
# Tool-name threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_messages_carry_tool_name_metadata(fake_tools):
    """Tool Messages built by the loop expose metadata['tool_name'] for transcripts."""
    from vibe.tools.tool_system import ToolResult

    tool_call = {"id": "call_1", "function": {"name": "web_search", "arguments": "{}"}}
    result = ToolResult(success=True, content="3 results")

    llm = _make_llm("done")
    loop = QueryLoop(llm_client=llm, tool_system=fake_tools, max_iterations=1, stream=False)
    loop.tool_executor.execute = AsyncMock(return_value=[result])

    await loop._process_tool_response(
        FakeLLMResponse(content="", tool_calls=[tool_call]), metrics=None
    )
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].metadata == {"tool_name": "web_search"}
    assert tool_msgs[0].tool_call_id == "call_1"
