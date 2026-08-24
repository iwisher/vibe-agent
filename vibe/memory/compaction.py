"""Lesson compaction — merge clusters of similar lessons into principle pages.

Lesson pages accumulate one per session (ACE-style delta items). Left alone,
near-duplicate lessons pile up and dilute retrieval, and naive multi-iteration
experience learning collapses (arXiv:2606.04703) — principle-level lessons are
what survive. The LessonCompactor clusters lesson pages by keyword word-overlap
over title+tags (the same measure as the reflection curator's title dedup; no
embeddings, no new deps), then makes ONE LLM call per cluster to synthesize a
principle-level page. Following ACE, counters are the memory: the merged page
sums the members' helpful/harmful counters and unions their citations, and
takes the max generality. Members are marked ``archived`` (never deleted) with
a ``{"type": "superseded"}`` citation, which excludes them from prompt
injection via ``is_page_injectable``.

Triggered manually via ``vibe memory wiki compact`` (no surprise LLM spend).
Error policy: ``compact()`` never raises; per-cluster failures skip the
cluster, leave members untouched, and are recorded in the report.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from vibe.memory.pageindex import index_wiki_page
from vibe.memory.reflection import (
    _read_counter,
    _read_generality,
    _render_lesson_content,
    _strip_counters,
    _topic_tags,
)
from vibe.memory.wiki import is_page_injectable

if TYPE_CHECKING:
    from vibe.core.config import ReflectionConfig

logger = logging.getLogger(__name__)

_LESSON_KINDS = frozenset({"pitfall", "procedure", "tip"})
# Tags excluded from clustering words: every lesson page carries them.
_GENERIC_TAGS = _LESSON_KINDS | {"lesson"}

# Per-member content bound in the synthesis prompt.
_MEMBER_MAX_CHARS = 800

_COMPACTION_PROMPT_TEMPLATE = """You are a lesson compaction engine. The lessons below \
were learned by an agent across separate sessions and cluster around the same topic.

Synthesize them into ONE principle-level lesson: the most general rule that \
subsumes every member. Keep it in the form "When X, do Y because Z" — never a \
restatement of what any single task was.

Rules:
- The title must capture the shared principle, not any single instance.
- applies_when describes the situation where the lesson should be recalled.
- Respond with ONLY a JSON object, no markdown code fences, no extra text:

{{"title": "...", "lesson": "...", "applies_when": "..."}}

LESSONS:
{members}
"""


@dataclass
class ClusterMerge:
    """Record of one merged cluster."""

    merged_page_id: str
    title: str
    member_ids: list[str]


@dataclass
class CompactionReport:
    """Summary of one compaction run."""

    lesson_pages: int = 0
    clusters_found: int = 0  # clusters at/above min cluster size (merge candidates)
    clusters_merged: int = 0
    clusters_skipped: int = 0  # candidates skipped on LLM/write errors
    merges: list[ClusterMerge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _word_overlap(a: set[str], b: set[str]) -> float:
    """Word-set overlap — the reflection curator's measure: |A ∩ B| / max(|A|, |B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _page_words(page: Any) -> set[str]:
    """Clustering word set: title tokens plus non-generic tags."""
    words = set(re.findall(r"[a-z0-9]+", str(getattr(page, "title", "")).lower()))
    for tag in getattr(page, "tags", None) or []:
        tag = str(tag).lower()
        if tag and tag not in _GENERIC_TAGS:
            words.add(tag)
    return words


def _lesson_kind(page: Any) -> str:
    """The kind tag of a lesson page (pitfall|procedure|tip); defaults to tip."""
    for tag in getattr(page, "tags", None) or []:
        if tag in _LESSON_KINDS:
            return tag
    return "tip"


class LessonCompactor:
    """Merge clusters of similar lesson pages into principle-level pages.

    Thread-safety: stateless — safe to use from multiple coroutines.
    Error policy: ``compact()`` never raises.
    """

    def __init__(
        self,
        wiki: Any,
        pageindex: Any,
        llm_client: Any,
        config: "ReflectionConfig | None" = None,
    ) -> None:
        self.wiki = wiki
        self.pageindex = pageindex
        self.llm_client = llm_client
        self.config = config

    async def compact(self) -> CompactionReport:
        """Cluster lesson pages and merge each sufficiently large cluster.

        Returns a CompactionReport with clusters found/merged/skipped. Never
        raises.
        """
        report = CompactionReport()
        try:
            pages = await self._collect_lesson_pages()
            report.lesson_pages = len(pages)
            min_cluster = self._cfg_int("compact_min_cluster", 3)
            overlap_threshold = self._cfg_float("compact_overlap", 0.5)
            clusters = self._cluster(pages, overlap_threshold)
            candidates = [c for c in clusters if len(c) >= min_cluster]
            report.clusters_found = len(candidates)
            for cluster in candidates:
                merged = await self._merge_cluster(cluster, report)
                if merged is None:
                    report.clusters_skipped += 1
                else:
                    report.clusters_merged += 1
                    report.merges.append(merged)
            if report.clusters_merged:
                logger.info(
                    "Lesson compaction merged %d cluster(s) across %d lesson pages",
                    report.clusters_merged,
                    report.lesson_pages,
                )
        except Exception as e:
            logger.warning("Lesson compaction failed (non-fatal): %s", e)
            report.errors.append(str(e))
        return report

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    async def _collect_lesson_pages(self) -> list[Any]:
        """All injectable lesson-tagged pages (archived/contradicted excluded)."""
        if self.wiki is None or not hasattr(self.wiki, "list_pages"):
            return []
        pages = await self.wiki.list_pages(tag="lesson")
        return [p for p in pages if is_page_injectable(p)]

    def _cluster(self, pages: list[Any], overlap_threshold: float) -> list[list[Any]]:
        """Greedy single-linkage clustering over title+tag word overlap."""
        clusters: list[list[Any]] = []
        # Deterministic order so reports and archives are reproducible
        for page in sorted(
            pages, key=lambda p: (str(getattr(p, "date_created", "")), str(getattr(p, "id", "")))
        ):
            words = _page_words(page)
            for cluster in clusters:
                if any(
                    _word_overlap(words, _page_words(member)) >= overlap_threshold
                    for member in cluster
                ):
                    cluster.append(page)
                    break
            else:
                clusters.append([page])
        return clusters

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    async def _merge_cluster(
        self, cluster: list[Any], report: CompactionReport
    ) -> ClusterMerge | None:
        """Synthesize and write the merged page, then archive the members."""
        try:
            synthesis = await self._synthesize(cluster)
            if synthesis is None:
                return None

            # ACE: counters are the memory — sum them; union citations by
            # session id; generality is the members' max.
            helpful = sum(_read_counter(getattr(p, "content", None), "helpful") for p in cluster)
            harmful = sum(_read_counter(getattr(p, "content", None), "harmful") for p in cluster)
            generalities = [
                g
                for p in cluster
                if (g := _read_generality(getattr(p, "content", None))) is not None
            ]
            generality = max(generalities) if generalities else None

            citations: list[dict] = []
            seen_sessions: set[str] = set()
            for p in cluster:
                for citation in getattr(p, "citations", None) or []:
                    session = citation.get("session") if isinstance(citation, dict) else None
                    if session and session not in seen_sessions:
                        seen_sessions.add(session)
                        citations.append(citation)

            kind = Counter(_lesson_kind(p) for p in cluster).most_common(1)[0][0]
            member_ids = [str(p.id) for p in cluster]

            content = _render_lesson_content(
                {
                    "lesson": synthesis["lesson"],
                    "applies_when": synthesis.get("applies_when", ""),
                    "kind": kind,
                    "generality": generality,
                    "supersedes": member_ids,
                },
                helpful=helpful,
                harmful=harmful,
            )

            tags = ["lesson", kind]
            for tag in _topic_tags(synthesis["title"]):
                if tag not in tags:
                    tags.append(tag)
            for p in cluster:
                for tag in getattr(p, "tags", None) or []:
                    tag = str(tag).lower()
                    if tag not in _GENERIC_TAGS and tag not in tags:
                        tags.append(tag)

            page = await self.wiki.create_page(
                title=synthesis["title"],
                content=content,
                tags=tags,
                status="draft",
                citations=citations,
            )
            # Make the merged principle immediately routable into future prompts
            index_wiki_page(self.pageindex, page)

            # Archive members — never delete. The superseded citation records
            # which merged page replaced them; the archived status excludes
            # them from prompt injection via is_page_injectable.
            for member in cluster:
                try:
                    await self.wiki.update_page(
                        page_id=member.id,
                        status="archived",
                        citations=[{"type": "superseded", "superseded_by": page.id}],
                    )
                except Exception as archive_err:
                    logger.debug(
                        "Failed to archive superseded lesson %s (non-fatal): %s",
                        member.id,
                        archive_err,
                    )
                    report.errors.append(f"archive {member.id}: {archive_err}")

            return ClusterMerge(merged_page_id=page.id, title=page.title, member_ids=member_ids)
        except Exception as e:
            logger.warning("Lesson cluster merge failed (non-fatal): %s", e)
            report.errors.append(str(e))
            return None

    async def _synthesize(self, cluster: list[Any]) -> dict | None:
        """One LLM call synthesizing a principle-level lesson from members."""
        member_blocks: list[str] = []
        for i, page in enumerate(cluster, 1):
            body = _strip_counters(getattr(page, "content", ""))
            if len(body) > _MEMBER_MAX_CHARS:
                body = body[: _MEMBER_MAX_CHARS - 1].rstrip() + "…"
            member_blocks.append(f"{i}. {getattr(page, 'title', '')}\n{body}")
        prompt = _COMPACTION_PROMPT_TEMPLATE.format(members="\n\n".join(member_blocks))

        raw = await self._call_llm(prompt)
        if not raw:
            return None

        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if not text.startswith("{"):
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                text = match.group()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Compaction synthesis is not valid JSON: %s", e)
            return None
        if not isinstance(data, dict):
            return None
        title = str(data.get("title", "")).strip()
        lesson = str(data.get("lesson", "")).strip()
        if not title or not lesson:
            return None
        return {
            "title": title,
            "lesson": lesson,
            "applies_when": str(data.get("applies_when", "")).strip(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> str | None:
        """Call the LLM with the synthesis prompt. Returns raw response or None."""
        try:
            client = self.llm_client
            if client is None:
                return None
            if hasattr(client, "complete"):
                response = await client.complete(prompt)
                if hasattr(response, "content"):
                    return response.content
                if isinstance(response, str):
                    return response
            # Fallback: try chat-style interface
            if hasattr(client, "chat"):
                response = await client.chat([{"role": "user", "content": prompt}])
                if hasattr(response, "content"):
                    return response.content
                if isinstance(response, str):
                    return response
            logger.warning("LLM client has no compatible interface for compaction")
            return None
        except Exception as e:
            logger.warning("LLM compaction call failed: %s", e)
            return None

    def _cfg_int(self, name: str, default: int) -> int:
        """Read an int knob from the ReflectionConfig (mock/None-safe)."""
        try:
            return int(getattr(self.config, name, default))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, name: str, default: float) -> float:
        """Read a float knob from the ReflectionConfig (mock/None-safe)."""
        try:
            return float(getattr(self.config, name, default))
        except (TypeError, ValueError):
            return default
