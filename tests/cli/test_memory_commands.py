"""Unit tests for CLI memory status command.

Covers: output formatting with mocked wiki/telemetry, graceful handling when
wiki not initialized.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from vibe.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# memory status command
# ---------------------------------------------------------------------------


def test_memory_status_with_wiki(tmp_path):
    """memory status should print a table with wiki stats."""
    # Create a fake wiki directory with some pages
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # Create a verified page
    verified_page = wiki_dir / "verified-page.md"
    verified_page.write_text(
        "---\n"
        "id: v-page-001\n"
        "title: Verified Page\n"
        "slug: verified-page\n"
        "status: verified\n"
        "date_created: 2026-04-20\n"
        "last_updated: 2026-04-26\n"
        "tags: [test]\n"
        "citations: []\n"
        "ttl_days: 30\n"
        "---\n\n"
        "This is verified content.\n"
    )

    # Create a draft page
    draft_page = wiki_dir / "draft-page.md"
    draft_page.write_text(
        "---\n"
        "id: d-page-001\n"
        "title: Draft Page\n"
        "slug: draft-page\n"
        "status: draft\n"
        "date_created: 2026-04-25\n"
        "last_updated: 2026-04-26\n"
        "tags: [test, draft]\n"
        "citations: []\n"
        "ttl_days: 30\n"
        "---\n\n"
        "This is draft content.\n"
    )

    # Create slug index
    index_path = wiki_dir / ".slug_index.json"
    index_path.write_text(
        json.dumps({
            "slug_to_id": {
                "verified-page": "v-page-001",
                "draft-page": "d-page-001",
            }
        })
    )

    with patch("vibe.cli.main._get_wiki") as mock_get_wiki:
        mock_wiki = MagicMock()
        mock_wiki.base_path = str(wiki_dir)
        mock_wiki.db = None  # No telemetry DB
        mock_wiki.get_status_counts = AsyncMock(return_value={"total": 2, "verified": 1, "draft": 1})
        mock_get_wiki.return_value = mock_wiki

        result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "Tripartite Memory Status" in result.output
        assert "Total pages" in result.output
        assert "Verified" in result.output
        assert "Draft" in result.output
        assert "2" in result.output  # total pages


def test_memory_status_empty_wiki(tmp_path):
    """memory status should handle empty wiki gracefully."""
    wiki_dir = tmp_path / "empty_wiki"
    wiki_dir.mkdir()

    index_path = wiki_dir / ".slug_index.json"
    index_path.write_text(json.dumps({"slug_to_id": {}}))

    with patch("vibe.cli.main._get_wiki") as mock_get_wiki:
        mock_wiki = MagicMock()
        mock_wiki.base_path = str(wiki_dir)
        mock_wiki.db = None
        mock_wiki.get_status_counts = AsyncMock(return_value={"total": 0, "verified": 0, "draft": 0})
        mock_get_wiki.return_value = mock_wiki

        result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "Tripartite Memory Status" in result.output
        assert "0" in result.output  # zero pages


def test_memory_status_with_telemetry(tmp_path):
    """memory status should include telemetry stats when DB is available."""
    wiki_dir = tmp_path / "wiki_with_telemetry"
    wiki_dir.mkdir()

    # Create one page
    page = wiki_dir / "page.md"
    page.write_text(
        "---\n"
        "id: p-001\n"
        "title: Test Page\n"
        "slug: page\n"
        "status: verified\n"
        "date_created: 2026-04-20\n"
        "last_updated: 2026-04-26\n"
        "tags: []\n"
        "citations: []\n"
        "ttl_days: 30\n"
        "---\n\nContent\n"
    )

    index_path = wiki_dir / ".slug_index.json"
    index_path.write_text(json.dumps({"slug_to_id": {"page": "p-001"}}))

    # Mock DB with telemetry data
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone = MagicMock(return_value=(5, 12.5))  # 5 sessions, 12.5s avg
    mock_db.conn.execute = MagicMock(return_value=mock_cursor)

    with patch("vibe.cli.main._get_wiki") as mock_get_wiki:
        mock_wiki = MagicMock()
        mock_wiki.base_path = str(wiki_dir)
        mock_wiki.db = mock_db
        mock_wiki.get_status_counts = AsyncMock(return_value={"total": 1, "verified": 1, "draft": 0})
        mock_get_wiki.return_value = mock_wiki

        result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "Sessions" in result.output
        assert "5" in result.output
        assert "12.5" in result.output
