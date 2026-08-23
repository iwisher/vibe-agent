"""Unit tests for CLI memory status command.

Covers: output formatting with mocked wiki/telemetry, graceful handling when
wiki not initialized.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
        json.dumps(
            {
                "slug_to_id": {
                    "verified-page": "v-page-001",
                    "draft-page": "d-page-001",
                }
            }
        )
    )

    with patch("vibe.cli.main._get_wiki") as mock_get_wiki:
        mock_wiki = MagicMock()
        mock_wiki.base_path = str(wiki_dir)
        mock_wiki.db = None  # No telemetry DB
        mock_wiki.get_status_counts = AsyncMock(
            return_value={"total": 2, "verified": 1, "draft": 1}
        )
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
        mock_wiki.get_status_counts = AsyncMock(
            return_value={"total": 0, "verified": 0, "draft": 0}
        )
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
        mock_wiki.get_status_counts = AsyncMock(
            return_value={"total": 1, "verified": 1, "draft": 0}
        )
        mock_get_wiki.return_value = mock_wiki

        result = runner.invoke(app, ["memory", "status"])
        assert result.exit_code == 0
        assert "Sessions" in result.output
        assert "5" in result.output
        assert "12.5" in result.output


# ---------------------------------------------------------------------------
# memory wiki compact command
# ---------------------------------------------------------------------------


def _patch_compact(report):
    """Patch the wiki, LLM factory, pageindex, and compactor for compact tests."""
    from vibe.memory.compaction import LessonCompactor  # noqa: F401

    mock_wiki = MagicMock()
    mock_wiki.base_path = "/tmp/unused"

    compactor = MagicMock()
    compactor.compact = AsyncMock(return_value=report)

    return (
        patch("vibe.cli.main._get_wiki", return_value=mock_wiki),
        patch("vibe.core.query_loop_factory.QueryLoopFactory"),
        patch("vibe.memory.pageindex.PageIndex"),
        patch("vibe.memory.compaction.LessonCompactor", return_value=compactor),
    )


def test_wiki_compact_prints_summary():
    """wiki compact runs the LessonCompactor and prints a Rich summary."""
    from vibe.memory.compaction import ClusterMerge, CompactionReport

    report = CompactionReport(
        lesson_pages=3,
        clusters_found=1,
        clusters_merged=1,
        merges=[
            ClusterMerge(
                merged_page_id="abcdef1234567890",
                title="Pin base images before debugging builds",
                member_ids=["id1aaaaaaaaaaa", "id2bbbbbbbbbbbb", "id3cccccccccccc"],
            )
        ],
    )
    wiki_patch, factory_patch, index_patch, compactor_patch = _patch_compact(report)
    with wiki_patch, factory_patch, index_patch, compactor_patch:
        result = runner.invoke(app, ["memory", "wiki", "compact"])

    assert result.exit_code == 0
    assert "Lesson compaction complete" in result.output
    assert "Lesson pages scanned: 3" in result.output
    assert "Merged: 1" in result.output
    assert "Skipped: 0" in result.output
    assert "Pin base images before debugging builds" in result.output
    assert "id1aaaaa" in result.output  # archived member ids listed


def test_wiki_compact_no_clusters():
    """wiki compact with nothing to merge prints a dim note."""
    from vibe.memory.compaction import CompactionReport

    report = CompactionReport(lesson_pages=2)
    wiki_patch, factory_patch, index_patch, compactor_patch = _patch_compact(report)
    with wiki_patch, factory_patch, index_patch, compactor_patch:
        result = runner.invoke(app, ["memory", "wiki", "compact"])

    assert result.exit_code == 0
    assert "No lesson clusters large enough to compact" in result.output


# ---------------------------------------------------------------------------
# memory status lesson-compaction hint
# ---------------------------------------------------------------------------


def _mock_config_with_min_cluster(n):
    mock_config = MagicMock()
    mock_config.memory.reflection.compact_min_cluster = n
    return mock_config


def test_memory_status_lesson_compaction_hint(tmp_path):
    """memory status suggests compaction when lesson pages exceed the threshold."""
    wiki_dir = tmp_path / "wiki_hint"
    wiki_dir.mkdir()

    lesson_pages = [MagicMock(status="draft") for _ in range(10)]

    with (
        patch("vibe.cli.main._get_wiki") as mock_get_wiki,
        patch("vibe.cli.main.DEFAULT_CONFIG", _mock_config_with_min_cluster(3)),
    ):
        mock_wiki = MagicMock()
        mock_wiki.base_path = str(wiki_dir)
        mock_wiki.db = None
        mock_wiki.get_status_counts = AsyncMock(
            return_value={"total": 10, "verified": 0, "draft": 10, "archived": 0}
        )
        mock_wiki.list_pages = AsyncMock(return_value=lesson_pages)
        mock_get_wiki.return_value = mock_wiki

        result = runner.invoke(app, ["memory", "status"])

    assert result.exit_code == 0
    assert "vibe memory wiki compact" in result.output


def test_memory_status_no_hint_below_threshold(tmp_path):
    """No hint when lesson pages are at or below min_cluster * 3."""
    wiki_dir = tmp_path / "wiki_no_hint"
    wiki_dir.mkdir()

    lesson_pages = [MagicMock(status="draft") for _ in range(3)]

    with (
        patch("vibe.cli.main._get_wiki") as mock_get_wiki,
        patch("vibe.cli.main.DEFAULT_CONFIG", _mock_config_with_min_cluster(3)),
    ):
        mock_wiki = MagicMock()
        mock_wiki.base_path = str(wiki_dir)
        mock_wiki.db = None
        mock_wiki.get_status_counts = AsyncMock(
            return_value={"total": 3, "verified": 0, "draft": 3, "archived": 0}
        )
        mock_wiki.list_pages = AsyncMock(return_value=lesson_pages)
        mock_get_wiki.return_value = mock_wiki

        result = runner.invoke(app, ["memory", "status"])

    assert result.exit_code == 0
    assert "wiki compact" not in result.output
