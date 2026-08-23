"""Unit tests for LessonCompactor — lesson lifecycle compaction (B1).

Covers: cluster formation over title+tag word overlap, merged page content
(summed counters, unioned citations, max generality, supersedes record),
member archiving (never deleted, excluded from injection), min-cluster skip,
never-raises error policy, and PageIndex re-indexing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.config import ReflectionConfig
from vibe.memory import compaction as compaction_mod
from vibe.memory.compaction import LessonCompactor
from vibe.memory.pageindex import PageIndex
from vibe.memory.reflection import _read_counter, _read_generality
from vibe.memory.wiki import LLMWiki, is_page_injectable

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMResponse:
    content: str


_SYNTHESIS_JSON = json.dumps(
    {
        "title": "Pin base images before debugging builds",
        "lesson": "When container builds break unexpectedly, pin base images by digest "
        "first, because floating tags mask every fix.",
        "applies_when": "A container build or deploy regresses without code changes",
    }
)


def _lesson_content(kind: str, generality: int | None, helpful: int, harmful: int) -> str:
    parts = ["Some reusable lesson text.", "", f"**Kind:** {kind}"]
    if generality is not None:
        parts.append(f"generality: {generality}")
    parts += ["", f"helpful: {helpful}", f"harmful: {harmful}"]
    return "\n".join(parts)


async def _seed_lesson(
    wiki: LLMWiki,
    title: str,
    *,
    tags: list[str],
    kind: str = "procedure",
    generality: int | None = 4,
    helpful: int = 1,
    harmful: int = 0,
    session: str,
):
    return await wiki.create_page(
        title=title,
        content=_lesson_content(kind, generality, helpful, harmful),
        tags=["lesson", kind, *tags],
        status="draft",
        citations=[{"session": session}],
    )


async def _seed_docker_cluster(wiki: LLMWiki):
    """Three lesson pages that cluster at the default 0.5 overlap."""
    p1 = await _seed_lesson(
        wiki,
        "Pin base image digests",
        tags=["docker"],
        helpful=2,
        harmful=0,
        generality=3,
        session="s1",
    )
    p2 = await _seed_lesson(
        wiki,
        "Pin base image versions",
        tags=["docker"],
        helpful=1,
        harmful=1,
        generality=4,
        session="s2",
    )
    p3 = await _seed_lesson(
        wiki,
        "Pin docker base images",
        tags=["docker", "pin"],
        helpful=3,
        harmful=0,
        generality=5,
        session="s1",
    )
    return [p1, p2, p3]


@pytest.fixture
def wiki(tmp_path):
    return LLMWiki(base_path=tmp_path / "wiki")


@pytest.fixture
def pageindex(tmp_path):
    return PageIndex(index_path=tmp_path / "index.json")


@pytest.fixture
def llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=FakeLLMResponse(content=_SYNTHESIS_JSON))
    return client


@pytest.fixture
def compactor(wiki, pageindex, llm):
    return LessonCompactor(
        wiki=wiki, pageindex=pageindex, llm_client=llm, config=ReflectionConfig()
    )


# ---------------------------------------------------------------------------
# Clustering + merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_merges_cluster(compactor, wiki, llm):
    members = await _seed_docker_cluster(wiki)

    report = await compactor.compact()

    assert report.errors == []
    assert report.lesson_pages == 3
    assert report.clusters_found == 1
    assert report.clusters_merged == 1
    assert report.clusters_skipped == 0
    assert llm.complete.await_count == 1  # exactly one synthesis call per cluster

    assert len(report.merges) == 1
    merge = report.merges[0]
    assert set(merge.member_ids) == {p.id for p in members}

    merged = await wiki.get_page(merge.merged_page_id)
    assert merged is not None
    assert merged.title == "Pin base images before debugging builds"
    assert merged.status == "draft"
    # ACE: counters are summed, generality is the max
    assert _read_counter(merged.content, "helpful") == 6
    assert _read_counter(merged.content, "harmful") == 1
    assert _read_generality(merged.content) == 5
    # Citations are unioned by session id (s1 appears twice in members)
    sessions = {c.get("session") for c in merged.citations if c.get("session")}
    assert sessions == {"s1", "s2"}
    # The merged page records which pages it superseded
    for member in members:
        assert member.id in merged.content
    assert "supersedes:" in merged.content
    # Tags: lesson + merged kind + topic tags
    assert "lesson" in merged.tags
    assert "procedure" in merged.tags
    assert "docker" in merged.tags


@pytest.mark.asyncio
async def test_compact_archives_members_and_excludes_from_injection(compactor, wiki):
    members = await _seed_docker_cluster(wiki)

    report = await compactor.compact()
    merged_id = report.merges[0].merged_page_id

    for member in members:
        archived = await wiki.get_page(member.id)  # never deleted
        assert archived is not None
        assert archived.status == "archived"
        assert not is_page_injectable(archived)
        assert any(
            c.get("type") == "superseded" and c.get("superseded_by") == merged_id
            for c in archived.citations
        )


@pytest.mark.asyncio
async def test_compact_forms_disjoint_clusters(wiki, pageindex, llm):
    json_b = json.dumps(
        {
            "title": "Commit atomic changes",
            "lesson": "When changes span concerns, split commits into atomic units.",
            "applies_when": "A diff mixes unrelated changes",
        }
    )
    llm.complete = AsyncMock(
        side_effect=[FakeLLMResponse(_SYNTHESIS_JSON), FakeLLMResponse(json_b)]
    )
    await _seed_docker_cluster(wiki)
    for i, title in enumerate(
        [
            "Commit small atomic changes",
            "Commit atomic changes often",
            "Commit atomic changes early",
        ]
    ):
        await _seed_lesson(wiki, title, tags=["git"], session=f"g{i}")

    compactor = LessonCompactor(wiki=wiki, pageindex=pageindex, llm_client=llm)
    report = await compactor.compact()

    assert report.lesson_pages == 6
    assert report.clusters_found == 2
    assert report.clusters_merged == 2
    assert llm.complete.await_count == 2
    titles = {m.title for m in report.merges}
    assert titles == {"Pin base images before debugging builds", "Commit atomic changes"}


@pytest.mark.asyncio
async def test_compact_skips_cluster_below_min_size(compactor, wiki, llm):
    p1 = await _seed_lesson(wiki, "Pin base image digests", tags=["docker"], session="s1")
    p2 = await _seed_lesson(wiki, "Pin base image versions", tags=["docker"], session="s2")

    report = await compactor.compact()

    assert report.lesson_pages == 2
    assert report.clusters_found == 0
    assert report.clusters_merged == 0
    llm.complete.assert_not_awaited()
    # Members untouched
    for p in (p1, p2):
        page = await wiki.get_page(p.id)
        assert page.status == "draft"
        assert is_page_injectable(page)


@pytest.mark.asyncio
async def test_compact_min_cluster_from_config(wiki, pageindex, llm):
    await _seed_lesson(wiki, "Pin base image digests", tags=["docker"], session="s1")
    await _seed_lesson(wiki, "Pin base image versions", tags=["docker"], session="s2")
    compactor = LessonCompactor(
        wiki=wiki,
        pageindex=pageindex,
        llm_client=llm,
        config=ReflectionConfig(compact_min_cluster=2),
    )
    report = await compactor.compact()
    assert report.clusters_found == 1
    assert report.clusters_merged == 1


@pytest.mark.asyncio
async def test_compact_skips_archived_and_contradicted_pages(compactor, wiki, llm):
    members = await _seed_docker_cluster(wiki)
    # Archive one member up front: only 2 injectable pages remain (< min cluster)
    await wiki.update_page(page_id=members[0].id, status="archived")

    report = await compactor.compact()

    assert report.lesson_pages == 2
    assert report.clusters_found == 0
    llm.complete.assert_not_awaited()

    # Contradiction-flagged pages are excluded too
    for page_id in (members[1].id, members[2].id):
        await wiki.update_page(
            page_id=page_id, citations=[{"type": "contradiction_flag", "detected_at": "2026-08-22"}]
        )
    report = await compactor.compact()
    assert report.lesson_pages == 0
    assert report.clusters_found == 0


@pytest.mark.asyncio
async def test_compact_second_run_is_stable(compactor, wiki):
    await _seed_docker_cluster(wiki)
    first = await compactor.compact()
    assert first.clusters_merged == 1

    # The merged principle is the only injectable lesson left — nothing to merge
    second = await compactor.compact()
    assert second.lesson_pages == 1
    assert second.clusters_found == 0
    assert second.clusters_merged == 0


@pytest.mark.asyncio
async def test_compact_reindexes_merged_page(compactor, wiki, monkeypatch):
    await _seed_docker_cluster(wiki)
    spy = MagicMock()
    monkeypatch.setattr(compaction_mod, "index_wiki_page", spy)

    report = await compactor.compact()

    assert report.clusters_merged == 1
    assert spy.call_count == 1
    indexed_page = spy.call_args.args[1]
    assert indexed_page.id == report.merges[0].merged_page_id


# ---------------------------------------------------------------------------
# Error policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_llm_failure_skips_cluster(compactor, wiki, llm):
    members = await _seed_docker_cluster(wiki)
    llm.complete = AsyncMock(side_effect=RuntimeError("boom"))

    report = await compactor.compact()  # never raises

    assert report.clusters_found == 1
    assert report.clusters_merged == 0
    assert report.clusters_skipped == 1
    # Members untouched on failure
    for p in members:
        page = await wiki.get_page(p.id)
        assert page.status == "draft"
        assert is_page_injectable(page)


@pytest.mark.asyncio
async def test_compact_malformed_synthesis_skips_cluster(compactor, wiki, llm):
    await _seed_docker_cluster(wiki)
    llm.complete = AsyncMock(return_value=FakeLLMResponse(content="not json at all"))

    report = await compactor.compact()

    assert report.clusters_merged == 0
    assert report.clusters_skipped == 1


@pytest.mark.asyncio
async def test_compact_never_raises_on_wiki_error(pageindex, llm):
    wiki = MagicMock()
    wiki.list_pages = AsyncMock(side_effect=RuntimeError("db gone"))
    compactor = LessonCompactor(wiki=wiki, pageindex=pageindex, llm_client=llm)

    report = await compactor.compact()  # never raises

    assert report.lesson_pages == 0
    assert report.clusters_found == 0
    assert report.errors != []
