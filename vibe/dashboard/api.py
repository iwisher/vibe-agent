"""FastAPI app for the React Trace Dashboard.

Read-only API with CORS restricted to same-origin.
Binds to 127.0.0.1 by default.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from vibe.dashboard.data import DashboardDataSource


def create_app(data_source: DashboardDataSource | None = None) -> FastAPI:
    """Create the FastAPI dashboard application."""
    app = FastAPI(title="Vibe Agent Dashboard", version="0.3.4")

    # CORS: only same-origin (localhost)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    ds = data_source or DashboardDataSource()

    # ------------------------------------------------------------------
    # Static files (React build output)
    # ------------------------------------------------------------------
    static_dir = Path(__file__).parent / "static"

    @app.get("/")
    async def root() -> FileResponse:
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        return (
            FileResponse(static_dir / "app.html")
            if (static_dir / "app.html").exists()
            else _fallback_html()
        )

    # ------------------------------------------------------------------
    # API: Sessions
    # ------------------------------------------------------------------
    @app.get("/api/sessions")
    async def list_sessions(
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        success: bool | None = Query(None),
    ) -> dict[str, Any]:
        sessions = await ds.list_sessions(limit=limit, offset=offset, success=success)
        return {
            "sessions": [
                {
                    "id": s.id,
                    "start_time": s.start_time,
                    "model": s.model,
                    "success": s.success,
                    "message_count": s.message_count,
                    "duration_seconds": s.duration_seconds,
                }
                for s in sessions
            ],
            "total": len(sessions),
        }

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        session = await ds.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.get("/api/sessions/stream")
    async def stream_sessions() -> StreamingResponse:
        """Server-Sent Events for live session updates."""

        async def event_generator():
            while True:
                stats = await ds.get_stats()
                yield f"data: {stats}\n\n"
                await asyncio.sleep(5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------------
    # API: Wiki
    # ------------------------------------------------------------------
    @app.get("/api/wiki/pages")
    async def list_wiki_pages(
        tag: str | None = Query(None),
        status: str | None = Query(None),
    ) -> dict[str, Any]:
        pages = await ds.list_wiki_pages(tag=tag, status=status)
        return {
            "pages": [
                {
                    "id": p.id,
                    "title": p.title,
                    "slug": p.slug,
                    "status": p.status,
                    "tags": p.tags,
                    "last_updated": p.last_updated,
                }
                for p in pages
            ],
            "total": len(pages),
        }

    @app.get("/api/wiki/graph")
    async def get_wiki_graph() -> dict[str, Any]:
        return await ds.get_wiki_graph()

    # ------------------------------------------------------------------
    # API: Skills
    # ------------------------------------------------------------------
    @app.get("/api/skills")
    async def list_skills() -> dict[str, Any]:
        skills = await ds.list_skills()
        return {
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "version": s.version,
                    "installed_at": s.installed_at,
                }
                for s in skills
            ],
            "total": len(skills),
        }

    # ------------------------------------------------------------------
    # API: Telemetry
    # ------------------------------------------------------------------
    @app.get("/api/telemetry")
    async def get_telemetry(hours: int = Query(24, ge=1, le=168)) -> dict[str, Any]:
        metrics = await ds.get_telemetry(hours=hours)
        return {
            "sessions_count": metrics.sessions_count,
            "avg_duration_seconds": metrics.avg_duration_seconds,
            "compactions_count": metrics.compactions_count,
            "errors_count": metrics.errors_count,
            "total_tokens": metrics.total_tokens,
            "total_cost": metrics.total_cost,
        }

    # ------------------------------------------------------------------
    # API: Stats
    # ------------------------------------------------------------------
    @app.get("/api/stats")
    async def get_stats() -> dict[str, Any]:
        stats = await ds.get_stats()
        return {
            "total_sessions": stats.total_sessions,
            "total_wiki_pages": stats.total_wiki_pages,
            "total_skills": stats.total_skills,
            "recent_errors": stats.recent_errors,
        }

    # ------------------------------------------------------------------
    # API: Config
    # ------------------------------------------------------------------
    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return {
            "version": "0.3.4",
            "dashboard_enabled": True,
            "features": {
                "sessions": True,
                "wiki": True,
                "skills": True,
                "telemetry": True,
                "graph": True,
            },
        }

    return app


def _fallback_html() -> dict[str, Any]:
    """Fallback when static files are not built yet."""
    return {
        "message": "Vibe Agent Dashboard",
        "status": "Static files not built. Run `npm run build` in dashboard/frontend.",
        "api": "/api/stats",
    }
