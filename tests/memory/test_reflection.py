"""Unit tests for TrajectoryReflector — post-session Reflector→Curator pipeline.

Covers: lesson page creation (tags/status/citations/routability), ACE-style
dedup/merge with helpful/harmful counters, ERROR pitfall marking, trivial
session skip, malformed JSON tolerance, LLM failure safety, tool-name
threading in transcripts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.config import ReflectionConfig
from vibe.memory.pageindex import PageIndex
from vibe.memory.reflection import TrajectoryReflector, _read_generality
from vibe.memory.wiki import LLMWiki

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    role: str
    content: str
    metadata: dict | None = None


@dataclass
class FakeLLMResponse:
    content: str


_LESSON_JSON = json.dumps(
    [
        {
            "title": "Pin base image digests",
            "lesson": "When pulling container base images, pin by digest instead of "
            "the latest tag, because upstream retagging silently breaks builds.",
            "applies_when": "A Dockerfile references a floating base image tag",
            "kind": "procedure",
        }
    ]
)

# A transcript long enough to clear the default skip heuristic
_LONG_QUERY = "How do I dockerize my Python web application with compose? " * 4
_LONG_ANSWER = "Use a pinned base image, a multi-stage build, and compose services. " * 4


def _make_messages(tool_metadata: dict | None = None) -> list[FakeMessage]:
    return [
        FakeMessage(role="user", content=_LONG_QUERY),
        FakeMessage(role="assistant", content="Let me inspect the project layout first."),
        FakeMessage(
            role="tool",
            content="Dockerfile found at repo root" + " with details" * 30,
            metadata=tool_metadata,
        ),
        FakeMessage(role="assistant", content=_LONG_ANSWER),
    ]


@pytest.fixture
def wiki(tmp_path):
    return LLMWiki(base_path=tmp_path / "wiki")


@pytest.fixture
def pageindex(tmp_path):
    return PageIndex(index_path=tmp_path / "index.json")


@pytest.fixture
def llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=FakeLLMResponse(content=_LESSON_JSON))
    return client


@pytest.fixture
def reflector(wiki, pageindex, llm):
    return TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )


# ---------------------------------------------------------------------------
# Lesson page creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_creates_lesson_page(reflector, wiki, pageindex):
    pages = await reflector.reflect(
        query="dockerize my app",
        messages=_make_messages(tool_metadata={"tool_name": "file_read"}),
        state="COMPLETED",
        session_id="sess-001",
    )
    assert len(pages) == 1
    page = pages[0]
    assert page.title == "Pin base image digests"
    assert "lesson" in page.tags
    assert "procedure" in page.tags
    assert page.status == "draft"
    assert any(c.get("session") == "sess-001" for c in page.citations)
    # COMPLETED sessions initialize the helpful counter
    assert "helpful: 1" in page.content
    assert "harmful: 0" in page.content
    assert "**Applies when:**" in page.content
    assert "**Kind:** procedure" in page.content


@pytest.mark.asyncio
async def test_reflect_lesson_immediately_routable(reflector, pageindex):
    pages = await reflector.reflect(
        query="dockerize my app",
        messages=_make_messages(),
        state="COMPLETED",
        session_id="sess-002",
    )
    assert len(pages) == 1
    # PageIndex (keyword routing, no LLM) must find the page right away
    nodes = await pageindex.route("docker base image digests pinning")
    routed_paths = {n.file_path for n in nodes}
    assert str(pages[0].path) in routed_paths


@pytest.mark.asyncio
async def test_reflect_caps_at_max_lessons(wiki, pageindex):
    many = json.dumps(
        [
            {"title": f"Lesson number {i}", "lesson": f"Rule {i} " * 20, "kind": "tip"}
            for i in range(6)
        ]
    )
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=FakeLLMResponse(content=many))
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10, max_lessons=2),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-cap"
    )
    assert len(pages) == 2


# ---------------------------------------------------------------------------
# Curator: dedup/merge with counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_merges_similar_lesson_and_increments_counter(
    reflector, wiki, pageindex, llm
):
    first = await reflector.reflect(
        query="dockerize my app",
        messages=_make_messages(),
        state="COMPLETED",
        session_id="sess-010",
    )
    assert len(first) == 1

    # A second session surfaces a similar lesson (same title, refined text)
    llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=json.dumps(
                [
                    {
                        "title": "Pin base image digests",
                        "lesson": "When choosing base images, pin the digest and record "
                        "it in lockfiles, because floating tags also break rollbacks.",
                        "applies_when": "Any container build",
                        "kind": "procedure",
                    }
                ]
            )
        )
    )
    second = await reflector.reflect(
        query="docker builds broke again",
        messages=_make_messages(),
        state="COMPLETED",
        session_id="sess-011",
    )
    assert len(second) == 1
    # Same page updated — no duplicate created
    assert second[0].id == first[0].id
    all_pages = await wiki.list_pages()
    assert len(all_pages) == 1
    merged = all_pages[0]
    assert "helpful: 2" in merged.content
    assert "harmful: 0" in merged.content
    # Lesson text merged additively, old text preserved
    assert "silently breaks builds" in merged.content
    assert "Refinement:" in merged.content
    assert "break rollbacks" in merged.content
    # Both sessions cited
    sessions = {c.get("session") for c in merged.citations}
    assert {"sess-010", "sess-011"} <= sessions


@pytest.mark.asyncio
async def test_reflect_merge_error_increments_harmful(reflector, wiki, llm):
    await reflector.reflect(
        query="q",
        messages=_make_messages(),
        state="COMPLETED",
        session_id="sess-020",
    )
    # Same lesson observed again in a failed session
    pages = await reflector.reflect(
        query="q",
        messages=_make_messages(),
        state="ERROR",
        session_id="sess-021",
    )
    assert len(pages) == 1
    assert "helpful: 1" in pages[0].content
    assert "harmful: 1" in pages[0].content


@pytest.mark.asyncio
async def test_reflect_never_merges_into_non_lesson_page(reflector, wiki, llm):
    # Pre-existing plain knowledge page with the same title
    await wiki.create_page(
        title="Pin base image digests",
        content="General Docker knowledge.",
        tags=["docker"],
    )
    pages = await reflector.reflect(
        query="q",
        messages=_make_messages(),
        state="COMPLETED",
        session_id="sess-030",
    )
    assert len(pages) == 1
    assert "lesson" in pages[0].tags
    # Two distinct pages: the plain one and the new lesson one
    assert len(await wiki.list_pages()) == 2


# ---------------------------------------------------------------------------
# ERROR trajectories → pitfall/harmful
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_error_session_marks_pitfall_and_harmful(wiki, pageindex, llm):
    llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=json.dumps(
                [
                    {
                        "title": "Do not force-push shared branches",
                        "lesson": "When a push is rejected, pull --rebase instead of "
                        "force-pushing, because force-push destroys teammates' work.",
                        "applies_when": "git push is rejected as non-fast-forward",
                        "kind": "pitfall",
                    }
                ]
            )
        )
    )
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="ERROR", session_id="sess-040"
    )
    assert len(pages) == 1
    page = pages[0]
    assert "pitfall" in page.tags
    assert "harmful: 1" in page.content
    assert "helpful: 0" in page.content


@pytest.mark.asyncio
async def test_reflect_error_session_coerces_invalid_kind_to_pitfall(wiki, pageindex, llm):
    llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=json.dumps(
                [{"title": "Check credentials first", "lesson": "x " * 60, "kind": "bogus"}]
            )
        )
    )
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="ERROR", session_id="sess-041"
    )
    assert len(pages) == 1
    assert "pitfall" in pages[0].tags
    assert "**Kind:** pitfall" in pages[0].content


# ---------------------------------------------------------------------------
# Skip heuristic / robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_skips_trivial_session(wiki, pageindex, llm):
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(),  # default min_transcript_chars=400
    )
    pages = await reflector.reflect(
        query="hi",
        messages=[
            FakeMessage(role="user", content="hi"),
            FakeMessage(role="assistant", content="hello!"),
        ],
        state="COMPLETED",
        session_id="sess-050",
    )
    assert pages == []
    llm.complete.assert_not_called()
    assert await wiki.list_pages() == []


@pytest.mark.asyncio
async def test_reflect_tolerates_malformed_json(reflector, wiki, llm):
    llm.complete = AsyncMock(return_value=FakeLLMResponse(content="not json at all"))
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-060"
    )
    assert pages == []
    assert await wiki.list_pages() == []


@pytest.mark.asyncio
async def test_reflect_parses_code_fenced_json_with_prose(reflector, llm):
    llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=f"Here are the lessons:\n```json\n{_LESSON_JSON}\n```\n"
        )
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-061"
    )
    assert len(pages) == 1
    assert pages[0].title == "Pin base image digests"


@pytest.mark.asyncio
async def test_reflect_llm_exception_no_write_no_raise(reflector, wiki, llm):
    llm.complete = AsyncMock(side_effect=RuntimeError("provider down"))
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-070"
    )
    assert pages == []
    assert await wiki.list_pages() == []


@pytest.mark.asyncio
async def test_reflect_wiki_write_failure_swallowed(reflector, wiki, llm, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(wiki, "create_page", _boom)
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-071"
    )
    assert pages == []


# ---------------------------------------------------------------------------
# Transcript building / tool-name threading
# ---------------------------------------------------------------------------


def test_transcript_uses_tool_name_from_metadata(reflector):
    messages = [
        FakeMessage(role="user", content="Search for flights"),
        FakeMessage(role="tool", content="3 results", metadata={"tool_name": "web_search"}),
        FakeMessage(role="assistant", content="Found 3 flights."),
    ]
    transcript = reflector._build_transcript(messages)
    assert "tool web_search: 3 results" in transcript


def test_transcript_falls_back_to_generic_label_without_metadata(reflector):
    messages = [FakeMessage(role="tool", content="ok")]
    transcript = reflector._build_transcript(messages)
    assert "tool result: ok" in transcript


def test_transcript_bounded(reflector):
    messages = [FakeMessage(role="user", content=f"message {i} " + "y" * 2000) for i in range(50)]
    transcript = reflector._build_transcript(messages)
    assert len(transcript) <= 12000 + 100  # bound + last line slack
    assert "[transcript truncated]" in transcript


# ---------------------------------------------------------------------------
# Generality gate (write-time critique)
# ---------------------------------------------------------------------------


def _generality_json(generality, **overrides) -> str:
    entry = {
        "title": "Pin base image digests",
        "lesson": "When pulling base images, pin by digest because tags drift. " * 3,
        "applies_when": "Writing Dockerfiles",
        "kind": "procedure",
        "generality": generality,
    }
    entry.update(overrides)
    return json.dumps([entry])


@pytest.mark.asyncio
async def test_generality_low_score_dropped(wiki, pageindex, llm):
    llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=_generality_json(
                1,
                title="Restart pod 7 in this outage",
                lesson="In this specific outage, restarting pod 7 fixed it. " * 3,
            )
        )
    )
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),  # default min_generality=3
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g01"
    )
    assert pages == []
    assert await wiki.list_pages() == []


@pytest.mark.asyncio
async def test_generality_high_score_accepted(wiki, pageindex, llm):
    llm.complete = AsyncMock(return_value=FakeLLMResponse(content=_generality_json(5)))
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g02"
    )
    assert len(pages) == 1
    assert "lesson" in pages[0].tags


@pytest.mark.asyncio
async def test_generality_missing_accepted_fail_open(reflector):
    # _LESSON_JSON carries no "generality" key at all
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g03"
    )
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_generality_unparseable_accepted_fail_open(wiki, pageindex, llm):
    llm.complete = AsyncMock(return_value=FakeLLMResponse(content=_generality_json("high")))
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g04"
    )
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_generality_threshold_from_config(wiki, pageindex, llm):
    llm.complete = AsyncMock(
        return_value=FakeLLMResponse(
            content=json.dumps(
                [
                    {
                        "title": "Check disk space before installs",
                        "lesson": "When an install fails opaquely, check disk space first. " * 3,
                        "kind": "tip",
                        "generality": 4,
                    },
                    {
                        "title": "State assumptions before acting",
                        "lesson": "When requirements are ambiguous, state assumptions "
                        "explicitly before acting, because silent guesses compound. " * 2,
                        "kind": "tip",
                        "generality": 5,
                    },
                ]
            )
        )
    )
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10, min_generality=5),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g05"
    )
    # Only the generality-5 lesson survives the stricter gate
    assert len(pages) == 1
    assert pages[0].title == "State assumptions before acting"


@pytest.mark.asyncio
async def test_generality_persisted_on_new_lesson(wiki, pageindex, llm):
    """A known generality score is written as a `generality: N` content line."""
    llm.complete = AsyncMock(return_value=FakeLLMResponse(content=_generality_json(4)))
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g06"
    )
    assert len(pages) == 1
    assert "generality: 4" in pages[0].content
    assert _read_generality(pages[0].content) == 4


@pytest.mark.asyncio
async def test_generality_line_absent_when_unknown(reflector):
    """Fail-open lessons (no generality score) carry no generality line."""
    # _LESSON_JSON carries no "generality" key at all
    pages = await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-g07"
    )
    assert len(pages) == 1
    assert "generality:" not in pages[0].content
    assert _read_generality(pages[0].content) is None


# ---------------------------------------------------------------------------
# Pivotal-turn annotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_pivotal_turn_included_in_prompt(reflector, llm):
    await reflector.reflect(
        query="q",
        messages=_make_messages(),
        state="ERROR",
        session_id="sess-p01",
        pivotal_turn=3,
    )
    prompt = llm.complete.call_args.args[0]
    assert "[3]" in prompt
    assert "derailed" in prompt


@pytest.mark.asyncio
async def test_reflect_without_pivotal_turn_omits_note(reflector, llm):
    await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-p02"
    )
    prompt = llm.complete.call_args.args[0]
    assert "derailed" not in prompt


# ---------------------------------------------------------------------------
# Usage feedback (record_usage)
# ---------------------------------------------------------------------------


async def _make_lesson_page(wiki, *, helpful: int = 1, harmful: int = 0):
    return await wiki.create_page(
        title="Pin base image digests",
        content=(
            "When pulling base images, pin by digest because tags drift.\n\n"
            f"helpful: {helpful}\nharmful: {harmful}"
        ),
        tags=["lesson", "procedure"],
    )


@pytest.mark.asyncio
async def test_record_usage_completed_bumps_helpful(reflector, wiki):
    page = await _make_lesson_page(wiki)
    await reflector.record_usage([page.id], "COMPLETED")
    updated = await wiki.get_page(page.id)
    assert "helpful: 2" in updated.content
    assert "harmful: 0" in updated.content
    # Body text preserved
    assert "pin by digest" in updated.content


@pytest.mark.asyncio
async def test_record_usage_error_bumps_harmful(reflector, wiki):
    page = await _make_lesson_page(wiki)
    await reflector.record_usage([page.id], "ERROR")
    updated = await wiki.get_page(page.id)
    assert "helpful: 1" in updated.content
    assert "harmful: 1" in updated.content


@pytest.mark.asyncio
async def test_record_usage_incomplete_no_change(reflector, wiki):
    page = await _make_lesson_page(wiki)
    await reflector.record_usage([page.id], "INCOMPLETE")
    updated = await wiki.get_page(page.id)
    assert "helpful: 1" in updated.content
    assert "harmful: 0" in updated.content


@pytest.mark.asyncio
async def test_record_usage_skips_non_lesson_pages(reflector, wiki):
    page = await wiki.create_page(
        title="Docker overview",
        content="General knowledge.\n\nhelpful: 3\nharmful: 0",
        tags=["docker"],
    )
    await reflector.record_usage([page.id], "COMPLETED")
    updated = await wiki.get_page(page.id)
    assert "helpful: 3" in updated.content
    assert "harmful: 0" in updated.content


@pytest.mark.asyncio
async def test_record_usage_tolerates_missing_pages(reflector, wiki):
    page = await _make_lesson_page(wiki)
    # Unknown ids are skipped without failing the valid ones
    await reflector.record_usage(["no-such-page-id", page.id], "COMPLETED")
    updated = await wiki.get_page(page.id)
    assert "helpful: 2" in updated.content


@pytest.mark.asyncio
async def test_record_usage_counters_accumulate_across_calls(reflector, wiki):
    page = await _make_lesson_page(wiki)
    await reflector.record_usage([page.id], "COMPLETED")
    await reflector.record_usage([page.id], "ERROR")
    await reflector.record_usage([page.id], "COMPLETED")
    updated = await wiki.get_page(page.id)
    assert "helpful: 3" in updated.content
    assert "harmful: 1" in updated.content


@pytest.mark.asyncio
async def test_record_usage_reindexes_updated_page(reflector, wiki, pageindex, monkeypatch):
    page = await _make_lesson_page(wiki)
    spy = MagicMock()
    monkeypatch.setattr("vibe.memory.reflection.index_wiki_page", spy)
    await reflector.record_usage([page.id], "COMPLETED")
    spy.assert_called_once()
    args = spy.call_args.args
    assert args[0] is pageindex
    assert args[1].id == page.id
    assert "helpful: 2" in args[1].content


@pytest.mark.asyncio
async def test_record_usage_preserves_generality_line(reflector, wiki):
    page = await wiki.create_page(
        title="Pin base image digests",
        content=(
            "When pulling base images, pin by digest because tags drift.\n\n"
            "generality: 4\n\nhelpful: 1\nharmful: 0"
        ),
        tags=["lesson", "procedure"],
    )
    await reflector.record_usage([page.id], "COMPLETED")
    updated = await wiki.get_page(page.id)
    assert "helpful: 2" in updated.content
    # The persisted generality score survives counter bumps (promotion needs it)
    assert _read_generality(updated.content) == 4


@pytest.mark.asyncio
async def test_record_usage_never_raises(reflector, wiki, monkeypatch):
    page = await _make_lesson_page(wiki)
    # Wiki read failure is swallowed
    monkeypatch.setattr(wiki, "get_page", AsyncMock(side_effect=RuntimeError("db gone")))
    await reflector.record_usage([page.id], "COMPLETED")
    monkeypatch.undo()
    # Empty/None inputs and unknown outcomes are silent no-ops
    await reflector.record_usage(None, "COMPLETED")
    await reflector.record_usage([], "COMPLETED")
    await reflector.record_usage([page.id], None)
    updated = await wiki.get_page(page.id)
    assert "helpful: 1" in updated.content


# ---------------------------------------------------------------------------
# Prompt template override (memory.reflection.prompt_template — harness evolution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflection_prompt_override_used(wiki, pageindex, llm):
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(
            min_transcript_chars=10,
            prompt_template="CUSTOM REFLECT max={max_lessons}\n{transcript}\nEND REFLECT",
        ),
    )
    await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-ovl"
    )
    prompt = llm.complete.call_args[0][0]
    assert prompt.startswith("CUSTOM REFLECT max=3")
    assert prompt.endswith("END REFLECT")
    assert "dockerize" in prompt  # transcript content threaded through


@pytest.mark.asyncio
async def test_reflection_prompt_override_unknown_placeholders_kept(wiki, pageindex, llm):
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(
            min_transcript_chars=10,
            prompt_template="CUSTOM {bogus} stays {max_lessons}\n{transcript}",
        ),
    )
    await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-ovl2"
    )
    prompt = llm.complete.call_args[0][0]
    assert "{bogus} stays 3" in prompt  # unknown placeholder preserved, no KeyError


@pytest.mark.asyncio
async def test_reflection_default_template_when_no_override(wiki, pageindex, llm):
    reflector = TrajectoryReflector(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(min_transcript_chars=10),
    )
    await reflector.reflect(
        query="q", messages=_make_messages(), state="COMPLETED", session_id="sess-ovl3"
    )
    prompt = llm.complete.call_args[0][0]
    assert "trajectory reflection engine" in prompt
