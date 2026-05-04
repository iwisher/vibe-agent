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

    def test_session_timeline_not_found(self, client):
        """Timeline returns empty for unknown session."""
        response = client.get("/api/sessions/nonexistent/timeline")
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
