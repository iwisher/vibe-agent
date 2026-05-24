"""Tests for dashboard API endpoints (Phase 5.1)."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibe.dashboard.server import app, DashboardState


@pytest.fixture
def client(tmp_path):
    """Create a test client with a temporary project root."""
    from fastapi.testclient import TestClient

    # Override the project root for testing
    original_cwd = os.getcwd()
    os.chdir(tmp_path)

    # Create test data directories
    (tmp_path / ".vibe").mkdir()
    (tmp_path / "wiki").mkdir()

    with TestClient(app) as c:
        yield c

    os.chdir(original_cwd)


@pytest.fixture
def session_db(tmp_path):
    """Create a SessionStore database with test data."""
    db_path = tmp_path / ".vibe" / "sessions.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS session_checkpoints (
            session_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            messages_json TEXT,
            plan_result_json TEXT,
            iteration INTEGER DEFAULT 0,
            feedback_retries INTEGER DEFAULT 0,
            model TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO session_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "sess-test-001",
            "RUNNING",
            json.dumps([{"role": "user", "content": "Hello"}]),
            json.dumps({"plan": "test"}),
            3,
            0,
            "qwen3.5-plus",
            "2026-05-01T10:00:00",
            "2026-05-01T10:05:00",
        ),
    )
    conn.commit()
    conn.close()
    return db_path


class TestDashboardAPI:
    """Test dashboard REST API endpoints."""

    def test_list_sessions_empty(self, client):
        """Sessions endpoint returns empty list when no DB exists."""
        response = client.get("/api/sessions")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_sessions_with_data(self, client, session_db):
        """Sessions endpoint returns session summaries."""
        response = client.get("/api/sessions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["session_id"] == "sess-test-001"
        assert data[0]["state"] == "RUNNING"
        assert data[0]["model"] == "qwen3.5-plus"
        assert data[0]["message_count"] == 1
        assert data[0]["duration_seconds"] > 0

    def test_session_timeline(self, client, session_db):
        """Timeline endpoint returns message events."""
        response = client.get("/api/sessions/sess-test-001/timeline")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["event_type"] == "message:user"
        assert "content_preview" in data[0]["data"]

    def test_session_messages(self, client, session_db):
        """Messages endpoint returns full messages for a session."""
        response = client.get("/api/sessions/sess-test-001/messages")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["role"] == "user"
        assert data[0]["content"] == "Hello"

    def test_session_messages_with_tool_calls(self, client, tmp_path):
        """Messages endpoint returns tool_calls in message data."""
        db_path = tmp_path / ".vibe" / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS session_checkpoints (
                session_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                messages_json TEXT,
                plan_result_json TEXT,
                iteration INTEGER DEFAULT 0,
                feedback_retries INTEGER DEFAULT 0,
                model TEXT,
                created_at TEXT,
                updated_at TEXT
            );
        """)
        messages = [
            {"role": "user", "content": "Call a tool"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "test_tool", "arguments": '{"x": 1}'}}
            ]},
            {"role": "tool", "content": "result", "tool_call_id": "tc-1"},
        ]
        conn.execute(
            "INSERT INTO session_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sess-tool-001",
                "COMPLETED",
                json.dumps(messages),
                json.dumps({}),
                1,
                0,
                "gpt-4",
                "2026-05-01T10:00:00",
                "2026-05-01T10:05:00",
            ),
        )
        conn.commit()
        conn.close()

        response = client.get("/api/sessions/sess-tool-001/messages")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert data[1]["tool_calls"][0]["function"]["name"] == "test_tool"
        assert data[2]["tool_call_id"] == "tc-1"

    def test_session_messages_not_found(self, client):
        """Messages returns empty for unknown session."""
        response = client.get("/api/sessions/nonexistent/messages")
        assert response.status_code == 200
        assert response.json() == []

    def test_session_messages_no_db(self, client, tmp_path):
        """Messages returns empty when no checkpoint DB exists."""
        # Ensure no sessions.db exists
        db_path = tmp_path / ".vibe" / "sessions.db"
        if db_path.exists():
            db_path.unlink()
        response = client.get("/api/sessions/any/messages")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_wiki_empty(self, client):
        """Wiki endpoint returns empty when no wiki directory."""
        response = client.get("/api/wiki")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_wiki_with_pages(self, client, tmp_path):
        """Wiki endpoint returns page summaries."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(exist_ok=True)
        page = wiki_dir / "test_page.md"
        page.write_text("""---
title: Test Page
tags: [test, example]
status: verified
---

This is a test wiki page with some content.
""")
        response = client.get("/api/wiki")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "test_page"
        assert data[0]["title"] == "Test Page"
        assert "test" in data[0]["tags"]
        assert data[0]["verification_status"] == "verified"
        assert data[0]["word_count"] > 0

    def test_list_skills_empty(self, client):
        """Skills endpoint returns empty when no skills directory."""
        response = client.get("/api/skills")
        assert response.status_code == 200
        assert response.json() == []

    def test_telemetry_empty(self, client):
        """Telemetry endpoint returns empty when no telemetry DB."""
        response = client.get("/api/telemetry")
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"] == []
        assert data["aggregates"] == {}

    def test_cors_headers(self, client):
        """CORS headers are present for localhost."""
        response = client.get("/api/sessions", headers={"Origin": "http://localhost:8080"})
        assert "access-control-allow-origin" in response.headers

    def test_websocket_connection(self, client):
        """WebSocket endpoint accepts connections."""
        with client.websocket_connect("/ws/live") as websocket:
            websocket.send_text("ping")
            data = websocket.receive_json()
            assert data["type"] == "pong"

    def test_run_server_returns_tuple_without_blocking(self, tmp_path):
        """run_server() must return (url, token) without blocking."""
        import os

        os.chdir(tmp_path)
        from vibe.dashboard.server import run_server

        url, token = run_server(host="127.0.0.1", port=9999, enable_auth=True)

        assert url == "http://127.0.0.1:9999/?token=" + token
        assert token is not None
        assert len(token) > 20
        # Should return immediately — not block on server startup

    def test_root_requires_auth(self, client, tmp_path):
        """Root path / must require a valid dashboard token."""
        os.chdir(tmp_path)
        from vibe.dashboard.server import DASHBOARD_TOKEN

        # Set a token to enable auth
        original_token = DASHBOARD_TOKEN
        from vibe.dashboard.server import _generate_dashboard_token

        token = _generate_dashboard_token()
        import vibe.dashboard.server as server_module

        server_module.DASHBOARD_TOKEN = token

        try:
            response = client.get("/")
            assert response.status_code == 401

            response = client.get(f"/?token={token}")
            # Should be 200 or 404 depending on whether static files exist;
            # the important thing is it's not 401
            assert response.status_code != 401
        finally:
            server_module.DASHBOARD_TOKEN = original_token
