"""FastAPI backend for the Vibe Agent trace dashboard (Phase 5.1).

Serves session data, wiki pages, telemetry, and skills via REST API.
Includes WebSocket endpoint for live session updates.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ────────────────────────────────
# Data models
# ────────────────────────────────


@dataclass
class SessionSummary:
    session_id: str
    state: str
    model: str
    iteration: int
    created_at: str
    updated_at: str
    message_count: int
    duration_seconds: float | None


@dataclass
class TimelineEvent:
    timestamp: str
    event_type: str
    data: dict[str, Any]


@dataclass
class WikiPageSummary:
    slug: str
    title: str
    tags: list[str]
    verification_status: str
    updated_at: str
    word_count: int


@dataclass
class TelemetryMetric:
    metric_name: str
    value: float
    timestamp: str
    session_id: str | None


@dataclass
class SkillSummary:
    name: str
    version: str
    description: str
    install_path: str
    last_used: str | None


# ────────────────────────────────
# Dashboard state
# ────────────────────────────────


class DashboardState:
    """Shared state for the dashboard server."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.db_dir = project_root / ".vibe"
        self.wiki_dir = project_root / "wiki"
        self.active_websockets: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    def _db_path(self, name: str) -> str:
        return str(self.db_dir / name)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected WebSocket clients."""
        disconnected: set[WebSocket] = set()
        async with self._lock:
            for ws in self.active_websockets:
                try:
                    await ws.send_json(message)
                except Exception:
                    disconnected.add(ws)
            self.active_websockets -= disconnected


# ────────────────────────────────
# Project root discovery
# ────────────────────────────────


def _find_project_root(start_path: Path | None = None) -> Path:
    """Find project root by looking for .vibe or .git markers.

    Traverses parent directories from start_path (or cwd) looking for
    .vibe/ or .git/ directories. Falls back to cwd if no marker found.
    """
    path = start_path or Path(os.getcwd())
    current = path.resolve()

    while current != current.parent:
        if (current / ".vibe").exists() or (current / ".git").exists():
            return current
        current = current.parent

    # Fallback: return the starting path
    return path.resolve()


# ────────────────────────────────
# FastAPI lifespan
# ────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[dict, None]:
    project_root = _find_project_root()
    state = DashboardState(project_root)
    app.state.dashboard = state
    yield {"dashboard": state}
    # Shutdown: close all websockets
    async with state._lock:
        for ws in list(state.active_websockets):
            try:
                await ws.close()
            except Exception:
                pass
        state.active_websockets.clear()


# ────────────────────────────────
# Build the app
# ────────────────────────────────

app = FastAPI(
    title="Vibe Agent Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: localhost only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ────────────────────────────────
# Simple token-based auth
# ────────────────────────────────

DASHBOARD_TOKEN: str | None = None


def _generate_dashboard_token() -> str:
    """Generate a one-time auth token for dashboard access."""
    import secrets

    return secrets.token_urlsafe(32)


def _require_token(request: Request) -> bool:
    """Check if request has valid dashboard token."""
    global DASHBOARD_TOKEN
    if DASHBOARD_TOKEN is None:
        return True  # No token set = no auth required (dev mode)

    # Check query param
    token = request.query_params.get("token", "")
    # Check header
    if not token:
        token = request.headers.get("x-dashboard-token", "")

    return token == DASHBOARD_TOKEN


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require token for all API and dashboard endpoints."""
    # Skip auth for health checks and static files only
    # Root / is NOT exempt — it serves the React app shell
    if request.url.path == "/health" or request.url.path.startswith("/static/"):
        return await call_next(request)

    if not _require_token(request):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing dashboard token"},
        )

    return await call_next(request)


# ────────────────────────────────
# API Endpoints
# ────────────────────────────────


def get_state(request: Request) -> DashboardState:
    return request.app.state.dashboard


def _load_sessions_sync(db_path: str) -> list[dict[str, Any]]:
    """Synchronous helper to load sessions from SQLite."""
    sessions = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT session_id, state, model, iteration, created_at, updated_at, "
            "messages_json FROM session_checkpoints ORDER BY updated_at DESC"
        )
        for row in cursor.fetchall():
            messages = json.loads(row["messages_json"] or "[]")
            created = datetime.fromisoformat(row["created_at"])
            updated = datetime.fromisoformat(row["updated_at"])
            duration = (updated - created).total_seconds()
            sessions.append(
                {
                    "session_id": row["session_id"],
                    "state": row["state"],
                    "model": row["model"],
                    "iteration": row["iteration"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "message_count": len(messages),
                    "duration_seconds": duration,
                }
            )
    return sessions


@app.get("/api/sessions", response_class=JSONResponse)
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    """List all sessions from SessionStore."""
    state = get_state(request)
    db_path = state._db_path("sessions.db")
    if not os.path.exists(db_path):
        return []

    return await asyncio.to_thread(_load_sessions_sync, db_path)


def _load_timeline_sync(db_path: str, session_id: str) -> list[dict[str, Any]]:
    """Synchronous helper to load session timeline from SQLite."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT messages_json, plan_result_json, state, iteration, updated_at "
            "FROM session_checkpoints WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return []

        timeline = []
        messages = json.loads(row["messages_json"] or "[]")
        for i, msg in enumerate(messages):
            timeline.append(
                {
                    "index": i,
                    "timestamp": row["updated_at"],
                    "event_type": f"message:{msg.get('role', 'unknown')}",
                    "data": {
                        "role": msg.get("role"),
                        "content_preview": _preview(msg.get("content", ""), 200),
                    },
                }
            )

        # Add state transition event
        plan = json.loads(row["plan_result_json"] or "null")
        if plan:
            timeline.append(
                {
                    "index": len(timeline),
                    "timestamp": row["updated_at"],
                    "event_type": "plan_result",
                    "data": {"plan": plan},
                }
            )

        return timeline


@app.get("/api/sessions/{session_id}/timeline")
async def session_timeline(session_id: str, request: Request) -> list[dict[str, Any]]:
    """Get message timeline for a session."""
    state = get_state(request)
    db_path = state._db_path("sessions.db")
    if not os.path.exists(db_path):
        return []

    return await asyncio.to_thread(_load_timeline_sync, db_path, session_id)


@app.get("/api/wiki")
async def list_wiki(request: Request) -> list[dict[str, Any]]:
    """List wiki pages."""
    state = get_state(request)
    if not state.wiki_dir.exists():
        return []

    pages = []
    for md_file in state.wiki_dir.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        # Parse frontmatter
        title = md_file.stem
        tags: list[str] = []
        status = "unverified"

        if content.startswith("---"):
            try:
                _, frontmatter, body = content.split("---", 2)
                for line in frontmatter.strip().split("\n"):
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"')
                    elif line.startswith("tags:"):
                        tags = [
                            t.strip()
                            for t in line.split(":", 1)[1].strip(" []").split(",")
                            if t.strip()
                        ]
                    elif line.startswith("status:"):
                        status = line.split(":", 1)[1].strip()
            except ValueError:
                body = content
        else:
            body = content

        pages.append(
            {
                "slug": md_file.stem,
                "title": title,
                "tags": tags,
                "verification_status": status,
                "updated_at": datetime.fromtimestamp(
                    md_file.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "word_count": len(body.split()),
            }
        )

    return sorted(pages, key=lambda p: p["updated_at"], reverse=True)


def _load_telemetry_sync(db_path: str) -> dict[str, Any]:
    """Synchronous helper to load telemetry from SQLite."""
    metrics = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT metric_name, value, timestamp, session_id FROM telemetry "
            "ORDER BY timestamp DESC LIMIT 1000"
        )
        for row in cursor.fetchall():
            metrics.append(
                {
                    "metric_name": row["metric_name"],
                    "value": row["value"],
                    "timestamp": row["timestamp"],
                    "session_id": row["session_id"],
                }
            )

    # Compute aggregates
    aggregates: dict[str, dict[str, Any]] = {}
    for m in metrics:
        name = m["metric_name"]
        if name not in aggregates:
            aggregates[name] = {"count": 0, "sum": 0.0, "min": float("inf"), "max": float("-inf")}
        aggregates[name]["count"] += 1
        aggregates[name]["sum"] += m["value"]
        aggregates[name]["min"] = min(aggregates[name]["min"], m["value"])
        aggregates[name]["max"] = max(aggregates[name]["max"], m["value"])

    for name in aggregates:
        aggregates[name]["avg"] = aggregates[name]["sum"] / aggregates[name]["count"]

    return {"metrics": metrics, "aggregates": aggregates}


@app.get("/api/telemetry")
async def get_telemetry(request: Request) -> dict[str, Any]:
    """Get aggregated telemetry metrics."""
    state = get_state(request)
    db_path = state._db_path("telemetry.db")
    if not os.path.exists(db_path):
        return {"metrics": [], "aggregates": {}}

    return await asyncio.to_thread(_load_telemetry_sync, db_path)


@app.get("/api/skills")
async def list_skills(request: Request) -> list[dict[str, Any]]:
    """List installed skills."""
    state = get_state(request)
    skills_dir = state.project_root / "skills"
    if not skills_dir.exists():
        return []

    skills = []
    for skill_file in skills_dir.rglob("SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        name = skill_file.parent.name
        version = "unknown"
        description = ""

        if content.startswith("---"):
            try:
                _, frontmatter, _ = content.split("---", 2)
                for line in frontmatter.strip().split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip('"')
                    elif line.startswith("version:"):
                        version = line.split(":", 1)[1].strip().strip('"')
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"')
            except ValueError:
                pass

        skills.append(
            {
                "name": name,
                "version": version,
                "description": description,
                "install_path": str(skill_file.parent),
                "last_used": None,  # Would need usage tracking
            }
        )

    return skills


# ────────────────────────────────
# WebSocket
# ────────────────────────────────


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    state = websocket.app.state.dashboard
    await websocket.accept()
    async with state._lock:
        state.active_websockets.add(websocket)
    try:
        while True:
            # Keep connection alive, broadcast happens via DashboardState.broadcast()
            data = await websocket.receive_text()
            # Echo back for ping/pong
            await websocket.send_json({"type": "pong", "received": data})
    except WebSocketDisconnect:
        async with state._lock:
            state.active_websockets.discard(websocket)
    except Exception:
        async with state._lock:
            state.active_websockets.discard(websocket)


# ────────────────────────────────
# Static files
# ────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

# ────────────────────────────────
# Helpers
# ────────────────────────────────


def _preview(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ────────────────────────────────
# Entry point
# ────────────────────────────────


def run_server(
    host: str = "127.0.0.1", port: int = 8080, enable_auth: bool = True
) -> tuple[str, str | None]:
    """Start the dashboard server.

    Args:
        host: Host to bind to.
        port: Port to listen on.
        enable_auth: If True, generates a one-time token printed to CLI.

    Returns:
        Tuple of (url, token) where token may be None if auth disabled.
    """
    global DASHBOARD_TOKEN

    token = None
    if enable_auth:
        token = _generate_dashboard_token()
        DASHBOARD_TOKEN = token

    url = f"http://{host}:{port}"
    if token:
        url += f"/?token={token}"

    return url, token


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
