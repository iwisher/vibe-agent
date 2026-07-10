"""Data access layer for the React Trace Dashboard.

Wraps TraceStore, LLMWiki, WikiGraph, SkillInstaller, and TelemetryCollector
with a unified async API for FastAPI endpoints.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


@dataclass
class SessionSummary:
    id: str
    start_time: str
    model: str
    success: bool
    message_count: int = 0
    duration_seconds: float = 0.0


@dataclass
class WikiPageSummary:
    id: str
    title: str
    slug: str
    status: str
    tags: list[str] = field(default_factory=list)
    last_updated: str = ""


@dataclass
class SkillSummary:
    id: str
    name: str
    version: str
    installed_at: str


@dataclass
class TelemetryMetrics:
    sessions_count: int
    avg_duration_seconds: float
    compactions_count: int
    errors_count: int
    total_tokens: int = 0
    total_cost: float = 0.0


@dataclass
class DashboardStats:
    total_sessions: int
    total_wiki_pages: int
    total_skills: int
    recent_errors: int


class DashboardDataSource:
    """Unified data access for the dashboard.

    All methods are async, wrapping sync underlying calls in
    run_in_threadpool where needed.
    """

    def __init__(
        self,
        trace_store: Any | None = None,
        wiki: Any | None = None,
        wiki_graph: Any | None = None,
        skill_installer: Any | None = None,
        telemetry: Any | None = None,
    ) -> None:
        self._trace_store = trace_store
        self._wiki = wiki
        self._wiki_graph = wiki_graph
        self._skill_installer = skill_installer
        self._telemetry = telemetry

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    async def list_sessions(
        self, limit: int = 50, offset: int = 0, success: bool | None = None
    ) -> list[SessionSummary]:
        if self._trace_store is None:
            return []
        rows = await run_in_threadpool(
            self._trace_store.get_sessions, limit=limit, offset=offset, success=success
        )
        result: list[SessionSummary] = []
        for row in rows:
            messages = []
            try:
                raw = row.get("messages", "[]")
                if isinstance(raw, str):
                    messages = json.loads(raw)
                elif isinstance(raw, list):
                    messages = raw
            except Exception:
                messages = []
            result.append(
                SessionSummary(
                    id=row.get("session_id", "unknown"),
                    start_time=row.get("start_time", ""),
                    model=row.get("model", "unknown"),
                    success=bool(row.get("success", False)),
                    message_count=len(messages),
                    duration_seconds=row.get("duration_seconds", 0.0) or 0.0,
                )
            )
        return result

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        if self._trace_store is None:
            return None
        sessions = await run_in_threadpool(self._trace_store.get_sessions, limit=1, success=None)
        for row in sessions:
            if row.get("session_id") == session_id:
                return dict(row)
        return None

    # ------------------------------------------------------------------
    # Wiki
    # ------------------------------------------------------------------
    async def list_wiki_pages(
        self, tag: str | None = None, status: str | None = None
    ) -> list[WikiPageSummary]:
        if self._wiki is None:
            return []
        pages = await self._wiki.list_pages(tag=tag, status=status)
        return [
            WikiPageSummary(
                id=p.id,
                title=p.title,
                slug=p.slug,
                status=p.status,
                tags=list(p.tags),
                last_updated=p.last_updated.isoformat()
                if hasattr(p.last_updated, "isoformat")
                else str(p.last_updated),
            )
            for p in pages
        ]

    async def get_wiki_graph(self) -> dict[str, list[dict]]:
        if self._wiki_graph is None:
            return {"nodes": [], "edges": []}
        nodes = await run_in_threadpool(self._wiki_graph.get_all_entities)
        edges = await run_in_threadpool(self._wiki_graph.get_all_edges)
        return {
            "nodes": [{"id": n.id, "label": n.label, "aliases": n.aliases} for n in nodes],
            "edges": [
                {"source": e.source_id, "target": e.target_id, "relation": e.relation}
                for e in edges
            ],
        }

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------
    async def list_skills(self) -> list[SkillSummary]:
        if self._skill_installer is None:
            return []
        installed = await run_in_threadpool(self._skill_installer.list_installed)
        result: list[SkillSummary] = []
        for skill_id, meta in installed.items():
            result.append(
                SkillSummary(
                    id=skill_id,
                    name=meta.get("name", skill_id),
                    version=meta.get("version", "unknown"),
                    installed_at=meta.get("installed_at", ""),
                )
            )
        return result

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    async def get_telemetry(self, hours: int = 24) -> TelemetryMetrics:
        if self._telemetry is None:
            return TelemetryMetrics(0, 0.0, 0, 0)
        summary = await run_in_threadpool(self._telemetry.get_summary, hours=hours)
        return TelemetryMetrics(
            sessions_count=summary.sessions_count,
            avg_duration_seconds=summary.avg_duration_seconds,
            compactions_count=summary.compactions_count,
            errors_count=summary.errors_count,
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    async def get_stats(self) -> DashboardStats:
        sessions = await self.list_sessions(limit=10000)
        pages = await self.list_wiki_pages()
        skills = await self.list_skills()
        telemetry = await self.get_telemetry(hours=24)
        return DashboardStats(
            total_sessions=len(sessions),
            total_wiki_pages=len(pages),
            total_skills=len(skills),
            recent_errors=telemetry.errors_count,
        )
