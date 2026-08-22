"""Trajectory reflection — post-session Reflector→Curator pipeline.

Test-time learning without weight updates. After a session ends, the
TrajectoryReflector distills a few reusable lessons from the trajectory
(Reflexion/ExpeL-style: failures are the richest signal), then an ACE-style
curator merges each lesson into the wiki as a small incremental delta item:
an existing similar lesson page is updated (helpful/harmful counter
incremented, lesson text refined additively) instead of rewritten — this
prevents "context collapse". Lessons live in ordinary wiki pages tagged
``lesson`` and are indexed into PageIndex immediately, so they become
routable into future prompts via the normal wiki-hint path.

Error policy: all public methods catch exceptions and return safe defaults.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from vibe.memory.extraction import _TOOL_SUMMARY_MAX_CHARS, KnowledgeExtractor
from vibe.memory.models import WikiPage  # noqa: F401 — re-exported for convenience
from vibe.memory.pageindex import index_wiki_page

if TYPE_CHECKING:
    from vibe.core.config import ReflectionConfig

logger = logging.getLogger(__name__)

_LESSON_KINDS = frozenset({"pitfall", "procedure", "tip"})

# Counter lines kept at the bottom of every lesson page body (WikiPage has no
# metadata field, so ACE-style helpful/harmful counters live in the content).
_COUNTER_RE_TEMPLATE = r"^{name}:\s*(\d+)\s*$"
_COUNTER_LINE_RE = re.compile(r"^(helpful|harmful):\s*\d+\s*$\n?", re.MULTILINE)

# Stopwords excluded from derived topic tags.
_TOPIC_STOPWORDS = frozenset(
    {"when", "from", "that", "this", "with", "before", "after", "your", "then", "than"}
)

# ---------------------------------------------------------------------------
# Reflection prompt
# ---------------------------------------------------------------------------

_REFLECTION_PROMPT_TEMPLATE = """You are a trajectory reflection engine. Analyze the \
agent session below and distill up to {max_lessons} reusable lessons.

Session outcome: {outcome}
Original user query: {query}

Rules:
- Each lesson must be a specific, reusable rule of the form "When X, do Y \
because Z" — never a restatement of what the task was.
- Failures and corrections are the richest signal: prefer lessons learned \
from errors, retries, and dead ends.
- Tool messages appear as compact `[i] tool <name>: <output>` summaries — \
use them to learn what the agent actually did.
- kind must be one of: "pitfall" (something to avoid), "procedure" (a \
reusable routine that worked), "tip" (a generalizable insight).
- Return at most {max_lessons} lessons. If nothing is generalizable, return [].
- Respond with ONLY a JSON array. No markdown code fences, no extra text.

Example:
[
  {{
    "title": "Verify server port before debugging code",
    "lesson": "When a dev server appears to ignore code changes, check that \
the port is not already served by a stale process before editing code, \
because a duplicate server masks every fix.",
    "applies_when": "A dev server does not reflect recent edits",
    "kind": "pitfall"
  }}
]

TRAJECTORY:
{transcript}
"""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _read_counter(content: str | None, name: str) -> int:
    """Read a ``helpful: N`` / ``harmful: N`` counter from page content."""
    match = re.search(_COUNTER_RE_TEMPLATE.format(name=name), content or "", re.MULTILINE)
    return int(match.group(1)) if match else 0


def _strip_counters(content: str | None) -> str:
    """Remove trailing counter lines so the body can be re-rendered."""
    return _COUNTER_LINE_RE.sub("", content or "").rstrip()


def _render_lesson_content(lesson: dict, *, helpful: int, harmful: int) -> str:
    """Render the structured body of a lesson page (counters at the bottom)."""
    parts = [lesson["lesson"], ""]
    if lesson.get("applies_when"):
        parts.append(f"**Applies when:** {lesson['applies_when']}")
    parts.append(f"**Kind:** {lesson['kind']}")
    parts.append("")
    parts.append(f"helpful: {helpful}")
    parts.append(f"harmful: {harmful}")
    return "\n".join(parts)


def _topic_tags(title: str, limit: int = 3) -> list[str]:
    """Derive up to ``limit`` topic tags from significant title words."""
    tags: list[str] = []
    for word in re.findall(r"[a-z0-9]+", title.lower()):
        if len(word) >= 4 and word not in _TOPIC_STOPWORDS and word not in tags:
            tags.append(word)
        if len(tags) >= limit:
            break
    return tags


# ---------------------------------------------------------------------------
# TrajectoryReflector
# ---------------------------------------------------------------------------


class TrajectoryReflector:
    """Distill reusable lessons from a finished session and curate them into the wiki.

    Thread-safety: stateless — safe to use from multiple coroutines.
    Error policy: ``reflect()`` never raises; all errors are caught and logged.
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def reflect(
        self,
        *,
        query: str,
        messages: list[Any],
        state: Any,
        session_id: str,
    ) -> list[WikiPage]:
        """Reflect on a finished trajectory and write lesson pages to the wiki.

        Args:
            query: The original user query (first user message).
            messages: Conversation messages (role/content[/metadata] attrs).
            state: Terminal QueryState (COMPLETED/INCOMPLETE/ERROR) or its name.
            session_id: Session UUID for citation tracking.

        Returns:
            List of created or updated WikiPages. Empty list on skip or error.
            Never raises.
        """
        try:
            outcome = getattr(state, "name", None) or str(state or "UNKNOWN")

            transcript = self._build_transcript(messages)
            min_chars = self._cfg_int("min_transcript_chars", 400)
            if len(transcript) < min_chars:
                logger.debug(
                    "Reflection skipped: trivial session (%d < %d transcript chars)",
                    len(transcript),
                    min_chars,
                )
                return []

            max_lessons = self._cfg_int("max_lessons", 3)
            prompt = _REFLECTION_PROMPT_TEMPLATE.format(
                max_lessons=max_lessons,
                outcome=outcome,
                query=(query or "")[:500],
                transcript=transcript,
            )
            raw = await self._call_llm(prompt)
            if not raw:
                return []

            lessons = self._parse_lessons(raw, outcome=outcome, max_lessons=max_lessons)
            if not lessons:
                return []

            pages: list[WikiPage] = []
            for lesson in lessons:
                try:
                    page = await self._curate_lesson(lesson, outcome=outcome, session_id=session_id)
                    if page is None:
                        continue
                    # Make the lesson immediately routable into future prompts
                    index_wiki_page(self.pageindex, page)
                    pages.append(page)
                except Exception as write_err:
                    logger.debug(
                        "Curator write failed for lesson '%s': %s",
                        lesson.get("title", ""),
                        write_err,
                    )
            if pages:
                logger.info(
                    "Trajectory reflection wrote %d lesson page(s) for session %s",
                    len(pages),
                    session_id,
                )
            return pages
        except Exception as e:
            logger.warning("Trajectory reflection failed (non-fatal): %s", e)
            return []

    # ------------------------------------------------------------------
    # Curator (ACE-style merge instead of rewrite)
    # ------------------------------------------------------------------

    async def _curate_lesson(
        self, lesson: dict, *, outcome: str, session_id: str
    ) -> WikiPage | None:
        """Merge a lesson into an existing similar page or create a new one.

        ERROR sessions mark the ``harmful`` counter (pitfall signal); other
        outcomes mark ``helpful``.
        """
        existing = await self._find_similar_lesson(lesson["title"])
        if existing is not None:
            return await self._merge_into_page(
                existing, lesson, outcome=outcome, session_id=session_id
            )

        is_error = outcome == "ERROR"
        content = _render_lesson_content(
            lesson, helpful=0 if is_error else 1, harmful=1 if is_error else 0
        )
        tags = ["lesson", lesson["kind"]]
        for tag in _topic_tags(lesson["title"]):
            if tag not in tags:
                tags.append(tag)
        return await self.wiki.create_page(
            title=lesson["title"],
            content=content,
            tags=tags,
            status="draft",
            citations=[{"session": session_id}],
        )

    async def _merge_into_page(
        self, page: WikiPage, lesson: dict, *, outcome: str, session_id: str
    ) -> WikiPage:
        """ACE-style delta update: increment the outcome counter and append the
        refined lesson text additively. Status is left to the wiki's gates."""
        helpful = _read_counter(page.content, "helpful")
        harmful = _read_counter(page.content, "harmful")
        if outcome == "ERROR":
            harmful += 1
        else:
            helpful += 1

        body = _strip_counters(page.content)
        new_text = lesson["lesson"]
        if new_text and new_text not in body:
            body = f"{body}\n\nRefinement: {new_text}" if body else new_text
        merged = f"{body}\n\nhelpful: {helpful}\nharmful: {harmful}"

        return await self.wiki.update_page(
            page_id=page.id,
            content=merged,
            citations=[{"session": session_id}],
        )

    async def _find_similar_lesson(self, title: str) -> WikiPage | None:
        """Find an existing lesson page with an exact or near-duplicate title."""
        if self.wiki is None or not hasattr(self.wiki, "search_pages"):
            return None
        try:
            results = await self.wiki.search_pages(title, limit=5)
        except Exception:
            return None
        # Only merge into other lesson pages — never into plain knowledge pages
        candidates = [p for p in results if "lesson" in (getattr(p, "tags", None) or [])]
        title_lower = title.lower()
        for page in candidates:
            if getattr(page, "title", "").lower() == title_lower:
                return page
        overlap_threshold = self._cfg_float("merge_title_overlap", 0.7)
        query_words = set(title_lower.split())
        for page in candidates:
            page_words = set(getattr(page, "title", "").lower().split())
            if page_words and query_words:
                overlap = len(page_words & query_words) / max(len(page_words), len(query_words))
                if overlap >= overlap_threshold:
                    return page
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_transcript(self, messages: list[Any]) -> str:
        """Build a compact transcript for the reflection prompt.

        Mirrors ``KnowledgeExtractor._build_transcript``: tool messages become
        one-line ``[i] tool <name>: <output>`` summaries (name from message
        metadata when present), system messages are skipped, and the whole
        transcript is bounded to ``max_transcript_chars`` (default ~12k).
        """
        max_chars = self._cfg_int("max_transcript_chars", 12000)
        lines: list[str] = []
        total_chars = 0
        for i, msg in enumerate(messages or []):
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                continue
            if role == "system":
                continue
            if role == "tool":
                tool_text = content.strip()
                if len(tool_text) > _TOOL_SUMMARY_MAX_CHARS:
                    tool_text = tool_text[: _TOOL_SUMMARY_MAX_CHARS - 1].rstrip() + "…"
                line = f"[{i}] tool {KnowledgeExtractor._tool_name_for(msg)}: {tool_text}"
            else:
                line = f"[{i}] {role}: {content.strip()}"
            if total_chars + len(line) > max_chars:
                lines.append("[transcript truncated]")
                break
            lines.append(line)
            total_chars += len(line)
        return "\n\n".join(lines)

    def _parse_lessons(self, raw: str, *, outcome: str, max_lessons: int) -> list[dict]:
        """Parse the LLM response into lesson dicts.

        Robust to markdown code fences, prose around the JSON array, and
        malformed entries. Invalid/missing kinds default to "pitfall" for
        ERROR sessions and "tip" otherwise.
        """
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # Find the JSON array, tolerating trailing prose
        if not text.startswith("["):
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                text = match.group()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Reflection response is not valid JSON: %s", e)
            return []
        if not isinstance(data, list):
            logger.warning("Reflection response is not a JSON array: %s", type(data))
            return []

        lessons: list[dict] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            lesson_text = str(entry.get("lesson", "")).strip()
            if not title or not lesson_text:
                continue
            kind = str(entry.get("kind", "")).strip().lower()
            if kind not in _LESSON_KINDS:
                kind = "pitfall" if outcome == "ERROR" else "tip"
            lessons.append(
                {
                    "title": title,
                    "lesson": lesson_text,
                    "applies_when": str(entry.get("applies_when", "")).strip(),
                    "kind": kind,
                }
            )
            if len(lessons) >= max_lessons:
                break
        return lessons

    async def _call_llm(self, prompt: str) -> str | None:
        """Call the LLM with the reflection prompt. Returns raw response or None."""
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
            logger.warning("LLM client has no compatible interface for reflection")
            return None
        except Exception as e:
            logger.warning("LLM reflection call failed: %s", e)
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
