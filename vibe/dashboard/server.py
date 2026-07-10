"""FastAPI backend for the Vibe Agent trace dashboard (Phase 5.1).

Serves session data, wiki pages, telemetry, and skills via REST API.
Includes WebSocket endpoint for live session updates.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
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

    # Transparent migration for legacy sessions database (Item 1)
    db_path = _get_traces_db_path(project_root)
    await asyncio.to_thread(_migrate_legacy_database_sync, project_root, db_path)

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


def _get_traces_db_path(project_root: Path) -> str:
    """Get the correct path to traces.db."""
    base = os.environ.get("VIBE_MEMORY_DIR")
    if base:
        return str(Path(base) / "traces.db")
    return str(Path.home() / ".vibe" / "memory" / "traces.db")


def _find_safe_backup_path(legacy_db: Path) -> Path:
    """Return a non-conflicting backup path, incrementing suffix if needed."""
    base = legacy_db.with_suffix(".db.backup")
    if not base.exists():
        return base
    counter = 1
    while True:
        candidate = legacy_db.with_suffix(f".db.backup.{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def _migrate_legacy_database_sync(project_root: Path, target_db_path: str) -> None:
    """Migrate active/incomplete checkpoints from legacy sessions.db to traces.db."""
    import logging

    logger = logging.getLogger(__name__)
    legacy_db = project_root / ".vibe" / "sessions.db"
    if not legacy_db.exists():
        return

    try:
        Path(target_db_path).parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(target_db_path) as conn_target:
            conn_target.execute(
                """
                CREATE TABLE IF NOT EXISTS session_checkpoints (
                    session_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    plan_result_json TEXT,
                    iteration INTEGER DEFAULT 0,
                    feedback_retries INTEGER DEFAULT 0,
                    model TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                """
            )

            with sqlite3.connect(str(legacy_db)) as conn_src:
                conn_src.row_factory = sqlite3.Row
                cursor = conn_src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='session_checkpoints'"
                )
                if not cursor.fetchone():
                    return

                cursor = conn_src.execute("SELECT * FROM session_checkpoints")
                rows = cursor.fetchall()
                for row in rows:
                    d = dict(row)
                    conn_target.execute(
                        """
                        INSERT OR IGNORE INTO session_checkpoints
                        (session_id, state, messages_json, plan_result_json, iteration,
                         feedback_retries, model, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            d.get("session_id"),
                            d.get("state"),
                            d.get("messages_json"),
                            d.get("plan_result_json"),
                            d.get("iteration", 0),
                            d.get("feedback_retries", 0),
                            d.get("model"),
                            d.get("created_at"),
                            d.get("updated_at"),
                        ),
                    )
            conn_target.commit()

        backup_path = _find_safe_backup_path(legacy_db)
        legacy_db.rename(backup_path)
        logger.info(f"Successfully migrated legacy sessions database to {target_db_path}")
    except Exception as e:
        logger.warning(f"Failed to migrate legacy sessions database: {e}")


def get_state(request: Request) -> DashboardState:
    return request.app.state.dashboard


def _load_sessions_sync(db_path: str) -> list[dict[str, Any]]:
    """Synchronous helper to load sessions from SQLite (active checkpoints & completed sessions)."""
    sessions = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # 1. Load active session checkpoints
        try:
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
        except sqlite3.OperationalError:
            pass

        # 2. Load completed sessions from TraceStore sessions table
        checkpoint_ids = {s["session_id"] for s in sessions}
        try:
            cursor = conn.execute(
                "SELECT id, start_time, end_time, success, model, error "
                "FROM sessions ORDER BY start_time DESC"
            )
            for row in cursor.fetchall():
                session_id = row["id"]
                if session_id in checkpoint_ids:
                    continue

                # Retrieve message count
                msg_count_cursor = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                    (session_id,),
                )
                msg_count = msg_count_cursor.fetchone()[0]

                # Compute duration
                created = datetime.fromisoformat(row["start_time"])
                updated = datetime.fromisoformat(row["end_time"])
                duration = (updated - created).total_seconds()

                state = "COMPLETED" if row["success"] else "ERROR"
                sessions.append(
                    {
                        "session_id": session_id,
                        "state": state,
                        "model": row["model"],
                        "iteration": 0,
                        "created_at": row["start_time"],
                        "updated_at": row["end_time"],
                        "message_count": msg_count,
                        "duration_seconds": duration,
                    }
                )
        except sqlite3.OperationalError:
            pass

    # Sort all merged sessions by updated_at descending
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions


@app.get("/api/sessions", response_class=JSONResponse)
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    """List all sessions from SessionStore and TraceStore."""
    state = get_state(request)
    db_path = _get_traces_db_path(state.project_root)
    if not os.path.exists(db_path):
        return []

    return await asyncio.to_thread(_load_sessions_sync, db_path)


def _load_timeline_sync(db_path: str, session_id: str) -> list[dict[str, Any]]:
    """Synchronous helper to load session timeline from SQLite."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # 1. Try active checkpoints
        try:
            cursor = conn.execute(
                "SELECT messages_json, plan_result_json, state, iteration, updated_at "
                "FROM session_checkpoints WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
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
        except sqlite3.OperationalError:
            pass

        # 2. Fall back to persistent messages table
        try:
            cursor = conn.execute(
                "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
            rows = cursor.fetchall()
            if rows:
                timeline = []
                for i, r in enumerate(rows):
                    timeline.append(
                        {
                            "index": i,
                            "timestamp": r["timestamp"],
                            "event_type": f"message:{r['role']}",
                            "data": {
                                "role": r["role"],
                                "content_preview": _preview(r["content"] or "", 200),
                            },
                        }
                    )
                return timeline
        except sqlite3.OperationalError:
            pass

        return []


@app.get("/api/sessions/{session_id}/timeline")
async def session_timeline(session_id: str, request: Request) -> list[dict[str, Any]]:
    """Get message timeline for a session."""
    state = get_state(request)
    db_path = _get_traces_db_path(state.project_root)
    if not os.path.exists(db_path):
        return []

    return await asyncio.to_thread(_load_timeline_sync, db_path, session_id)


def _load_messages_sync(db_path: str, session_id: str) -> list[dict[str, Any]]:
    """Synchronous helper to load full session messages from SQLite."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # 1. Try to load active checkpoint messages first
        try:
            cursor = conn.execute(
                "SELECT messages_json FROM session_checkpoints WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                messages = json.loads(row["messages_json"] or "[]")
                result = []
                for msg in messages:
                    entry = {
                        "role": msg.get("role", "unknown"),
                        "content": msg.get("content", ""),
                        "tool_calls": msg.get("tool_calls"),
                        "tool_call_id": msg.get("tool_call_id"),
                    }
                    result.append(entry)
                return result
        except sqlite3.OperationalError:
            pass

        # 2. Fall back to persistent messages table for completed sessions
        try:
            cursor = conn.execute(
                "SELECT role, content, tool_calls FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
            rows = cursor.fetchall()
            if rows:
                result = []
                for r in rows:
                    tool_calls = None
                    if r["tool_calls"]:
                        try:
                            tool_calls = json.loads(r["tool_calls"])
                        except Exception:
                            pass
                    result.append(
                        {
                            "role": r["role"],
                            "content": r["content"],
                            "tool_calls": tool_calls,
                            "tool_call_id": None,
                        }
                    )
                return result
        except sqlite3.OperationalError:
            pass

        return []


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, request: Request) -> list[dict[str, Any]]:
    """Get full messages for a session replay."""
    state = get_state(request)
    db_path = _get_traces_db_path(state.project_root)
    if not os.path.exists(db_path):
        return []

    return await asyncio.to_thread(_load_messages_sync, db_path, session_id)


@app.get("/api/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Get dashboard configuration."""
    return {"version": "0.3.5", "auth_enabled": DASHBOARD_TOKEN is not None}


@app.get("/api/stats")
async def get_stats(request: Request) -> dict[str, Any]:
    """Get aggregated dashboard stats."""
    state = get_state(request)

    # Count sessions
    sessions = []
    db_path = state._db_path("sessions.db")
    if os.path.exists(db_path):
        sessions = await asyncio.to_thread(_load_sessions_sync, db_path)

    # Count wiki pages
    wiki_pages = []
    if state.wiki_dir.exists():
        for md_file in state.wiki_dir.rglob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            title = md_file.stem
            if content.startswith("---"):
                try:
                    _, frontmatter, _ = content.split("---", 2)
                    for line in frontmatter.strip().split("\n"):
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"')
                except ValueError:
                    pass
            wiki_pages.append({"slug": md_file.stem, "title": title})

    # Count skills
    skills = []
    skills_dir = state.project_root / "skills"
    if skills_dir.exists():
        for skill_file in skills_dir.rglob("SKILL.md"):
            skills.append({"name": skill_file.parent.name})

    # Count recent errors from telemetry
    recent_errors = 0
    telemetry_db = state._db_path("telemetry.db")
    if os.path.exists(telemetry_db):
        telemetry = await asyncio.to_thread(_load_telemetry_sync, telemetry_db)
        recent_errors = len(
            [m for m in telemetry.get("metrics", []) if m.get("metric_name") == "error"]
        )

    return {
        "total_sessions": len(sessions),
        "total_wiki_pages": len(wiki_pages),
        "total_skills": len(skills),
        "recent_errors": recent_errors,
    }


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


@app.get("/api/wiki/{slug}")
async def get_wiki_page(slug: str, request: Request) -> dict[str, Any]:
    """Get a single wiki page by slug."""
    state = get_state(request)
    if not state.wiki_dir.exists():
        return {"error": "Wiki directory not found"}

    # Validate slug to prevent path traversal
    if not re.match(r"^[a-zA-Z0-9_-]+$", slug):
        return {"error": "Invalid slug format"}

    md_file = (state.wiki_dir / f"{slug}.md").resolve()
    wiki_dir_resolved = state.wiki_dir.resolve()

    # Ensure the resolved path is within wiki_dir
    try:
        md_file.relative_to(wiki_dir_resolved)
    except ValueError:
        return {"error": "Access denied"}

    if not md_file.exists():
        return {"error": "Page not found"}

    # Read file in thread pool to avoid blocking event loop
    try:
        content = await asyncio.to_thread(md_file.read_text, encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": "File is not valid UTF-8 text"}

    title = md_file.stem
    tags: list[str] = []
    status = "unverified"
    body = content

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

    # Get stat in thread pool
    try:
        stat = await asyncio.to_thread(md_file.stat)
        mtime = stat.st_mtime
    except OSError:
        mtime = 0

    return {
        "slug": slug,
        "title": title,
        "tags": tags,
        "verification_status": status,
        "content": body.strip(),
        "updated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        "word_count": len(body.split()),
    }


@app.post("/api/wiki/regenerate")
async def regenerate_wiki(request: Request) -> dict[str, Any]:
    """Regenerate wiki pages from session data.

    Scans sessions database for tool invocations and memory writes
    that could be turned into wiki pages.
    """
    # CSRF protection: require custom header
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JSONResponse(
            {"error": "CSRF protection: missing X-Requested-With header"}, status_code=403
        )

    state = get_state(request)

    # Ensure wiki directory exists
    state.wiki_dir.mkdir(parents=True, exist_ok=True)

    # Load sessions to extract knowledge
    sessions = []
    db_path = state._db_path("sessions.db")
    if os.path.exists(db_path):
        sessions = await asyncio.to_thread(_load_sessions_sync, db_path)

    pages_created = 0
    pages_updated = 0

    # For each session, extract potential wiki content
    def _generate_pages():
        nonlocal pages_created, pages_updated
        for session in sessions:
            raw_id = session.get("session_id")

            # Skip sessions with missing or invalid IDs
            if not raw_id:
                continue

            # Sanitize session_id to prevent path traversal
            session_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(raw_id))
            if not session_id:
                continue

            # Check if there's already a wiki page for this session topic
            # For now, create a summary page if it doesn't exist
            slug = f"session-{session_id[:8]}"
            md_file = state.wiki_dir / f"{slug}.md"

            # Use atomic file creation to avoid TOCTOU race condition
            try:
                with open(md_file, "x", encoding="utf-8") as f:
                    # Create a new wiki page from session data
                    content = f"""---
title: Session Summary {session_id[:8]}
tags: [auto-generated, session]
status: draft
---

# Session Summary

- **Session ID**: {session_id}
- **Model**: {session.get("model", "unknown")}
- **State**: {session.get("state", "unknown")}
- **Messages**: {session.get("message_count", 0)}
- **Duration**: {session.get("duration_seconds", 0):.1f}s

This page was auto-generated from session data.
"""
                    f.write(content)
                pages_created += 1
            except FileExistsError:
                pass

    # Run file generation in thread pool to avoid blocking event loop
    await asyncio.to_thread(_generate_pages)

    return {
        "success": True,
        "pages_created": pages_created,
        "pages_updated": pages_updated,
        "total_sessions_scanned": len(sessions),
        "wiki_dir": str(state.wiki_dir),
    }


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
# API: Research Papers
# ────────────────────────────────


@app.get("/api/research/papers")
async def list_research_papers(request: Request) -> list[dict[str, Any]]:
    """List curated research papers available in the dashboard."""
    return [
        {
            "id": "evox",
            "title": "EvoX: Meta-Evolution for Automated Discovery",
            "authors": [
                "Shu Liu",
                "Shubham Agarwal",
                "Monishwaran Maheswaran",
                "Mert Cemri",
                "Zhifei Li",
                "Qiuyang Mang",
                "Ashwin Naren",
                "Ethan Boneh",
                "Audrey Cheng",
                "Melissa Z. Pan",
                "Alexander Du",
                "Kurt Keutzer",
                "Alexandros G. Dimakis",
                "Koushik Sen",
                "Matei Zaharia",
                "Ion Stoica",
            ],
            "affiliations": ["UC Berkeley", "Stanford University", "Bespoke Labs"],
            "venue": "arXiv:2602.23413v1 [cs.LG]",
            "published": "2026-02-26",
            "url": "https://arxiv.org/pdf/2602.23413v1",
            "summary": (
                "EvoX introduces an adaptive evolution method that jointly evolves candidate "
                "solutions and the search strategies used to generate them. By meta-evolving "
                "the search strategy itself, EvoX dynamically shifts between exploration and "
                "exploitation across nearly 200 real-world optimization tasks, outperforming "
                "AlphaEvolve, OpenEvolve, GEPA, and ShinkaEvolve on the majority of benchmarks."
            ),
        }
    ]


@app.get("/api/research/papers/{paper_id}")
async def get_research_paper(paper_id: str, request: Request) -> dict[str, Any]:
    """Get full details for a curated research paper."""
    if paper_id != "evox":
        return JSONResponse({"error": "Paper not found"}, status_code=404)

    return {
        "id": "evox",
        "title": "EvoX: Meta-Evolution for Automated Discovery",
        "authors": [
            "Shu Liu",
            "Shubham Agarwal",
            "Monishwaran Maheswaran",
            "Mert Cemri",
            "Zhifei Li",
            "Qiuyang Mang",
            "Ashwin Naren",
            "Ethan Boneh",
            "Audrey Cheng",
            "Melissa Z. Pan",
            "Alexander Du",
            "Kurt Keutzer",
            "Alexandros G. Dimakis",
            "Koushik Sen",
            "Matei Zaharia",
            "Ion Stoica",
        ],
        "affiliations": ["UC Berkeley", "Stanford University", "Bespoke Labs"],
        "venue": "arXiv:2602.23413v1 [cs.LG]",
        "published": "2026-02-26",
        "url": "https://arxiv.org/pdf/2602.23413v1",
        "abstract": (
            "Recent work such as AlphaEvolve has shown that combining LLM-driven optimization "
            "with evolutionary search can effectively improve programs, prompts, and algorithms "
            "across domains. In this paradigm, previously evaluated solutions are reused to guide "
            "the model toward new candidate solutions. Crucially, the effectiveness of this "
            "evolution process depends on the search strategy: how prior solutions are selected "
            "and varied to generate new candidates. However, most existing methods rely on fixed "
            "search strategies with predefined knobs (e.g., explore–exploit ratios) that remain "
            "static throughout execution. While effective in some settings, these approaches "
            "often fail to adapt across tasks, or even within the same task as the search space "
            "changes over time."
        ),
        "problem": (
            "Existing LLM-driven evolutionary systems rely on fixed search strategies with "
            "hand-specified parameters. A fixed exploitation ratio or static diversity heuristic "
            "cannot adapt when the search landscape changes, causing stagnation and requiring "
            "manual retuning."
        ),
        "method": {
            "overview": (
                "EvoX frames LLM-driven optimization as a two-level evolution process: an inner "
                "loop that evolves candidate solutions, and an outer loop that evolves the search "
                "strategy governing generation."
            ),
            "steps": [
                {
                    "title": "Solution evolution under the current strategy",
                    "body": (
                        "Given the current database of evaluated candidates and an active search "
                        "strategy, EvoX constructs a generation context (parent, variation "
                        "operator, inspiration set), prompts the LLM generator, evaluates the new "
                        "candidate, and appends it to the population."
                    ),
                },
                {
                    "title": "Progress monitoring and strategy updates",
                    "body": (
                        "EvoX monitors improvement over a sliding window of W evaluations. When "
                        "progress Δ falls below a stagnation threshold τ, it triggers a strategy "
                        "update. Strategy efficacy is scored as "
                        "J(S) = (s_end − s_start) · log(1 + s_start) / √W, up-weighting progress "
                        "near the performance frontier."
                    ),
                },
                {
                    "title": "Meta-evolving the search strategy",
                    "body": (
                        "When stagnation is detected, EvoX selects a high-performing parent "
                        "strategy from a strategy database conditioned on the current "
                        "population descriptor φ(D_t), mutates it with an LLM, validates the "
                        "candidate, and deploys it without resetting the solution population."
                    ),
                },
            ],
        },
        "variation_operators": [
            {
                "name": "Local refinement",
                "purpose": "Fine-grained edits for exploitation (tune parameters, reorder logic).",
            },
            {
                "name": "Structural variation",
                "purpose": "Coarse-grained redesigns for exploration (switch algorithm families).",
            },
            {
                "name": "Free-form variation",
                "purpose": "No constraints on the edit direction.",
            },
        ],
        "results": [
            {
                "task": "Mathematical optimization",
                "finding": (
                    "Best or tied-best on 7 of 8 tasks under GPT-5 and all 8 under "
                    "Gemini-3.0-Pro; matches or exceeds AlphaEvolve on 5 of 7 comparable "
                    "tasks within 100 iterations."
                ),
            },
            {
                "task": "System performance optimization",
                "finding": (
                    "Exceeds human-best results on all six benchmarks (EPLB, PRISM, LLM-SQL, "
                    "Cloudcast, transaction scheduling, telemetry repair)."
                ),
            },
            {
                "task": "Signal processing case study",
                "finding": (
                    "Achieves a 34.1% higher final score than a static baseline by adaptively "
                    "switching from random search → greedy → stratified multi-objective → "
                    "UCB-guided structural variation → local refinement."
                ),
            },
        ],
        "contributions": [
            (
                "Formalizes LLM-driven optimization as a two-level process separating "
                "solution evolution from search strategy evolution."
            ),
            (
                "Introduces EvoX, which dynamically evolves search strategies based on "
                "observed optimization progress."
            ),
            (
                "Demonstrates consistent improvements over prior methods across nearly 200 "
                "problems and characterizes cost, scaling, and adaptation dynamics."
            ),
        ],
        "tags": ["evolutionary-search", "llm-optimization", "meta-learning", "automated-discovery"],
    }


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
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Serve index.html at root
    @app.get("/")
    async def root():
        return HTMLResponse(content=(static_dir / "index.html").read_text())

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

    Returns (url, token) where token is None if auth is disabled.
    """
    global DASHBOARD_TOKEN
    if enable_auth:
        DASHBOARD_TOKEN = _generate_dashboard_token()
    else:
        DASHBOARD_TOKEN = None

    url = f"http://{host}:{port}"
    if DASHBOARD_TOKEN:
        url += f"/?token={DASHBOARD_TOKEN}"

    return url, DASHBOARD_TOKEN
