"""Tests for the React Trace Dashboard backend API."""

import pytest
from fastapi.testclient import TestClient

from vibe.dashboard.api import create_app
from vibe.dashboard.data import (
    DashboardDataSource,
)


class FakeTraceStore:
    def __init__(self, sessions=None):
        self._sessions = sessions or []

    def get_sessions(self, limit=100, success=None, offset=0):
        rows = self._sessions
        if success is not None:
            rows = [r for r in rows if r.get("success") == success]
        return rows[offset : offset + limit]


class FakeWiki:
    def __init__(self, pages=None):
        self._pages = pages or []

    async def list_pages(self, tag=None, status=None):
        pages = self._pages
        if tag is not None:
            pages = [p for p in pages if tag in p.tags]
        if status is not None:
            pages = [p for p in pages if p.status == status]
        return pages


class FakePage:
    def __init__(self, id, title, slug, status, tags=None):
        self.id = id
        self.title = title
        self.slug = slug
        self.status = status
        self.tags = tags or []
        self.last_updated = "2024-01-01T00:00:00"


class FakeWikiGraph:
    def __init__(self, nodes=None, edges=None):
        self._nodes = nodes or []
        self._edges = edges or []

    def get_all_entities(self):
        return self._nodes

    def get_all_edges(self):
        return self._edges


class FakeEntity:
    def __init__(self, id, label, aliases=None):
        self.id = id
        self.label = label
        self.aliases = aliases or []


class FakeEdge:
    def __init__(self, source_id, target_id, relation):
        self.source_id = source_id
        self.target_id = target_id
        self.relation = relation


class FakeSkillInstaller:
    def __init__(self, skills=None):
        self._skills = skills or {}

    def list_installed(self):
        return self._skills


class FakeTelemetry:
    def __init__(self, summary=None):
        self._summary = summary

    def get_summary(self, hours=24):
        return self._summary


class FakeSummary:
    def __init__(self, sessions=0, duration=0.0, compactions=0, errors=0):
        self.sessions_count = sessions
        self.avg_duration_seconds = duration
        self.compactions_count = compactions
        self.errors_count = errors


@pytest.fixture
def client():
    ds = DashboardDataSource(
        trace_store=FakeTraceStore(
            [
                {
                    "session_id": "ses-1",
                    "start_time": "2024-01-01T00:00:00",
                    "model": "gpt-4",
                    "success": True,
                    "messages": "[{},{},{}]",
                    "duration_seconds": 5.5,
                },
                {
                    "session_id": "ses-2",
                    "start_time": "2024-01-01T01:00:00",
                    "model": "claude-3",
                    "success": False,
                    "messages": "[{}]",
                    "duration_seconds": 2.0,
                },
            ]
        ),
        wiki=FakeWiki(
            [
                FakePage("p1", "Python", "python", "verified", ["coding"]),
                FakePage("p2", "Finance", "finance", "pending", ["finance"]),
            ]
        ),
        wiki_graph=FakeWikiGraph(
            nodes=[FakeEntity("e1", "Python", ["py"])],
            edges=[FakeEdge("e1", "e1", "self")],
        ),
        skill_installer=FakeSkillInstaller(
            {
                "skill-1": {"name": "Test Skill", "version": "1.0.0", "installed_at": "2024-01-01"},
            }
        ),
        telemetry=FakeTelemetry(FakeSummary(sessions=10, duration=3.5, compactions=2, errors=1)),
    )
    app = create_app(data_source=ds)
    return TestClient(app)


class TestDashboardAPI:
    def test_get_stats(self, client):
        res = client.get("/api/stats")
        assert res.status_code == 200
        data = res.json()
        assert data["total_sessions"] == 2
        assert data["total_wiki_pages"] == 2
        assert data["total_skills"] == 1
        assert data["recent_errors"] == 1

    def test_list_sessions(self, client):
        res = client.get("/api/sessions?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 2
        assert len(data["sessions"]) == 2
        assert data["sessions"][0]["id"] == "ses-1"
        assert data["sessions"][0]["message_count"] == 3
        assert data["sessions"][0]["duration_seconds"] == 5.5

    def test_list_sessions_with_success_filter(self, client):
        res = client.get("/api/sessions?success=true")
        assert res.status_code == 200
        data = res.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["success"] is True

    def test_get_session(self, client):
        res = client.get("/api/sessions/ses-1")
        assert res.status_code == 200
        assert res.json()["session_id"] == "ses-1"

    def test_get_session_not_found(self, client):
        res = client.get("/api/sessions/nonexistent")
        assert res.status_code == 404

    def test_list_wiki_pages(self, client):
        res = client.get("/api/wiki/pages")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 2
        assert data["pages"][0]["title"] == "Python"

    def test_list_wiki_pages_with_tag_filter(self, client):
        res = client.get("/api/wiki/pages?tag=finance")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["pages"][0]["title"] == "Finance"

    def test_get_wiki_graph(self, client):
        res = client.get("/api/wiki/graph")
        assert res.status_code == 200
        data = res.json()
        assert len(data["nodes"]) == 1
        assert len(data["edges"]) == 1
        assert data["nodes"][0]["label"] == "Python"

    def test_list_skills(self, client):
        res = client.get("/api/skills")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["skills"][0]["name"] == "Test Skill"

    def test_get_telemetry(self, client):
        res = client.get("/api/telemetry")
        assert res.status_code == 200
        data = res.json()
        assert data["sessions_count"] == 10
        assert data["avg_duration_seconds"] == 3.5
        assert data["compactions_count"] == 2
        assert data["errors_count"] == 1

    def test_get_config(self, client):
        res = client.get("/api/config")
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == "0.3.4"
        assert data["dashboard_enabled"] is True

    def test_root_serves_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")

    def test_cors_headers(self, client):
        res = client.get("/api/stats", headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 200
        # CORS middleware is configured but may not echo origin for non-matching origins
        assert "access-control-allow-credentials" in res.headers
