"""Unit tests for LLMWiki storage layer.

Covers: CRUD, YAML frontmatter, slug generation, backlinks, expiration,
concurrency stress test (10 parallel writers, 0 corruption).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from vibe.memory.models import WikiPage
from vibe.memory.wiki import LLMWiki, _content_hash, _make_slug

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_wiki(tmp_path):
    return LLMWiki(base_path=tmp_path / "wiki")


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------


def test_make_slug_basic():
    assert _make_slug("Hello World") == "hello-world"


def test_make_slug_special_chars():
    assert _make_slug("Database Scaling & Logs!") == "database-scaling-logs"


def test_make_slug_underscores():
    assert _make_slug("my_page_title") == "my-page-title"


def test_make_slug_empty():
    assert _make_slug("") == "untitled"


def test_make_slug_numbers():
    assert _make_slug("Phase 1a Config") == "phase-1a-config"


def test_content_hash():
    h1 = _content_hash("hello world")
    h2 = _content_hash("hello world")
    h3 = _content_hash("hello worldX")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_page_basic(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Infrastructure Logs",
        content="Database scaling issues documented here.",
        tags=["database", "scaling"],
    )
    assert page.id
    assert page.slug == "infrastructure-logs"
    assert page.title == "Infrastructure Logs"
    assert page.status == "draft"
    assert page.tags == ["database", "scaling"]
    assert page.ttl_days == 30


@pytest.mark.asyncio
async def test_create_page_writes_file(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Test Page",
        content="Some content here.",
        tags=["test"],
    )
    assert page.path.exists()
    text = page.path.read_text()
    assert "---" in text
    assert "Test Page" in text
    assert "Some content here." in text


@pytest.mark.asyncio
async def test_get_page_by_id(tmp_wiki):
    created = await tmp_wiki.create_page(
        title="Get Test", content="Content", tags=["t1"]
    )
    fetched = await tmp_wiki.get_page(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.title == "Get Test"


@pytest.mark.asyncio
async def test_get_page_not_found(tmp_wiki):
    result = await tmp_wiki.get_page("nonexistent-uuid-1234")
    assert result is None


@pytest.mark.asyncio
async def test_get_page_by_slug(tmp_wiki):
    created = await tmp_wiki.create_page(
        title="Slug Test", content="Content", tags=[]
    )
    fetched = await tmp_wiki.get_page_by_slug("slug-test")
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_update_page_content(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Update Test", content="Original content.", tags=["a"]
    )
    updated = await tmp_wiki.update_page(page.id, content="Updated content.")
    assert updated.content == "Updated content."
    assert updated.last_updated == date.today().isoformat()


@pytest.mark.asyncio
async def test_update_page_tags(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Tag Update", content="Content", tags=["old"]
    )
    updated = await tmp_wiki.update_page(page.id, tags=["new1", "new2"])
    assert "new1" in updated.tags
    assert "old" not in updated.tags


@pytest.mark.asyncio
async def test_update_page_adds_citations(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Citation Test", content="Content", tags=[]
    )
    citation = {"session": "sess-001", "date": "2026-04-26", "summary": "Finding 1"}
    updated = await tmp_wiki.update_page(page.id, citations=[citation])
    assert len(updated.citations) == 1
    assert updated.citations[0]["session"] == "sess-001"


@pytest.mark.asyncio
async def test_update_preserves_unmodified_fields(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Preserve Test", content="Original", tags=["keep-me"]
    )
    updated = await tmp_wiki.update_page(page.id, content="Updated")
    assert "keep-me" in updated.tags
    assert updated.title == "Preserve Test"


@pytest.mark.asyncio
async def test_delete_page(tmp_wiki):
    page = await tmp_wiki.create_page(
        title="Delete Me", content="Content", tags=[]
    )
    path = page.path
    assert path.exists()

    result = await tmp_wiki.delete_page(page.id)
    assert result is True
    assert not path.exists()

    # Should not be findable after deletion
    fetched = await tmp_wiki.get_page(page.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_delete_nonexistent_page(tmp_wiki):
    result = await tmp_wiki.delete_page("nonexistent-id")
    assert result is False


@pytest.mark.asyncio
async def test_list_pages_empty(tmp_wiki):
    pages = await tmp_wiki.list_pages()
    assert pages == []


@pytest.mark.asyncio
async def test_list_pages_all(tmp_wiki):
    await tmp_wiki.create_page(title="Page A", content="A", tags=["tag1"])
    await tmp_wiki.create_page(title="Page B", content="B", tags=["tag2"])
    pages = await tmp_wiki.list_pages()
    assert len(pages) == 2


@pytest.mark.asyncio
async def test_list_pages_filter_by_tag(tmp_wiki):
    await tmp_wiki.create_page(title="Tagged", content="X", tags=["special"])
    await tmp_wiki.create_page(title="Untagged", content="Y", tags=["other"])
    pages = await tmp_wiki.list_pages(tag="special")
    assert len(pages) == 1
    assert pages[0].title == "Tagged"


@pytest.mark.asyncio
async def test_list_pages_filter_by_status(tmp_wiki):
    await tmp_wiki.create_page(title="Draft One", content="X", tags=[])
    pages = await tmp_wiki.list_pages(status="draft")
    assert len(pages) == 1
    verified_pages = await tmp_wiki.list_pages(status="verified")
    assert len(verified_pages) == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_pages_by_title(tmp_wiki):
    await tmp_wiki.create_page(title="Database Scaling", content="Load balancing details.", tags=[])
    await tmp_wiki.create_page(title="UI Design", content="User interface patterns.", tags=[])
    results = await tmp_wiki.search_pages("database")
    titles = [p.title for p in results]
    assert "Database Scaling" in titles
    assert "UI Design" not in titles


@pytest.mark.asyncio
async def test_search_pages_no_results(tmp_wiki):
    await tmp_wiki.create_page(title="Some Page", content="Content here.", tags=[])
    results = await tmp_wiki.search_pages("zxqjvm")
    assert results == []


@pytest.mark.asyncio
async def test_search_pages_by_tag(tmp_wiki):
    await tmp_wiki.create_page(title="Infra Page", content="Details.", tags=["infrastructure"])
    results = await tmp_wiki.search_pages("infrastructure")
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# Backlinks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_backlinks_basic(tmp_wiki):
    page_a = await tmp_wiki.create_page(
        title="Page Alpha",
        content="Some content",
        tags=[],
    )
    # Page B links to Page Alpha via [[page-alpha]]
    await tmp_wiki.create_page(
        title="Page Beta",
        content="This references [[page-alpha]] for more details.",
        tags=[],
    )

    backlinks = await tmp_wiki.get_backlinks(page_a.id)
    assert len(backlinks) == 1
    assert backlinks[0].title == "Page Beta"


@pytest.mark.asyncio
async def test_get_backlinks_empty(tmp_wiki):
    page = await tmp_wiki.create_page(title="Isolated", content="No links", tags=[])
    backlinks = await tmp_wiki.get_backlinks(page.id)
    assert backlinks == []


# ---------------------------------------------------------------------------
# Draft expiration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expire_drafts_old(tmp_wiki):
    """Draft pages older than cutoff should be deleted."""
    page = await tmp_wiki.create_page(title="Old Draft", content="Old", tags=[])

    # Manually set last_updated to 40 days ago
    old_date = (datetime.now() - timedelta(days=40)).date().isoformat()
    page_data = page.path.read_text()
    import yaml

    if page_data.startswith("---"):
        parts = page_data.split("---", 2)
        meta = yaml.safe_load(parts[1])
        meta["last_updated"] = old_date
        frontmatter_text = yaml.dump(meta, default_flow_style=False, allow_unicode=True)
        page.path.write_text(f"---\n{frontmatter_text}---\n\n{parts[2].strip()}\n")

    # Reset loaded flag to force re-read
    tmp_wiki._loaded = False

    count = await tmp_wiki.expire_drafts(cutoff_days=30)
    assert count == 1
    assert not page.path.exists()


@pytest.mark.asyncio
async def test_expire_drafts_recent_not_deleted(tmp_wiki):
    """Recent draft pages should NOT be deleted."""
    await tmp_wiki.create_page(title="Recent Draft", content="New content", tags=[])
    count = await tmp_wiki.expire_drafts(cutoff_days=30)
    assert count == 0


@pytest.mark.asyncio
async def test_expire_drafts_verified_not_deleted(tmp_wiki):
    """Verified pages should never be expired."""
    page = await tmp_wiki.create_page(title="Verified Page", content="Content", tags=[])
    # Mark as verified directly
    import yaml
    data = page.path.read_text()
    parts = data.split("---", 2)
    meta = yaml.safe_load(parts[1])
    meta["status"] = "verified"
    meta["last_updated"] = (datetime.now() - timedelta(days=40)).date().isoformat()
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True)
    page.path.write_text(f"---\n{fm}---\n\n{parts[2].strip()}\n")
    tmp_wiki._loaded = False

    count = await tmp_wiki.expire_drafts(cutoff_days=30)
    assert count == 0


# ---------------------------------------------------------------------------
# Quality gates — status promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_auto_promotes_to_verified(tmp_wiki):
    """Page with ≥2 citations from distinct sessions auto-promotes to verified."""
    page = await tmp_wiki.create_page(title="Auto Promote", content="Content", tags=[])
    assert page.status == "draft"

    # Add 2 citations from distinct sessions
    updated = await tmp_wiki.update_page(
        page.id,
        citations=[
            {"session": "sess-aaa", "date": "2026-04-25", "summary": "Session 1 finding"},
            {"session": "sess-bbb", "date": "2026-04-26", "summary": "Session 2 finding"},
        ],
    )
    assert updated.status == "verified"


@pytest.mark.asyncio
async def test_status_stays_draft_with_one_session(tmp_wiki):
    """Page with citations from only 1 session stays draft."""
    page = await tmp_wiki.create_page(title="Stay Draft", content="Content", tags=[])

    updated = await tmp_wiki.update_page(
        page.id,
        citations=[
            {"session": "sess-aaa", "date": "2026-04-25", "summary": "Only one session"},
        ],
    )
    assert updated.status == "draft"


# ---------------------------------------------------------------------------
# Concurrency stress test — 10 parallel writers, 0 corruption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writers_no_corruption(tmp_wiki):
    """10 concurrent create_page calls must all succeed and produce valid pages."""

    async def create_one(i: int) -> WikiPage:
        return await tmp_wiki.create_page(
            title=f"Concurrent Page {i}",
            content=f"Content for page {i} with unique text {i * 7}",
            tags=[f"tag{i % 3}"],
        )

    # Launch 10 concurrent writers
    results = await asyncio.gather(*[create_one(i) for i in range(10)])

    assert len(results) == 10
    # All IDs must be unique
    ids = [p.id for p in results]
    assert len(set(ids)) == 10, "Duplicate page IDs detected — concurrency corruption!"

    # All files must exist
    for page in results:
        assert page.path.exists(), f"Page file missing: {page.path}"

    # List should show all 10 pages
    all_pages = await tmp_wiki.list_pages()
    assert len(all_pages) == 10


@pytest.mark.asyncio
async def test_concurrent_updates_no_corruption(tmp_wiki):
    """Concurrent updates to the same page should not corrupt data."""
    page = await tmp_wiki.create_page(
        title="Shared Page", content="Initial content", tags=["shared"]
    )

    async def update_one(i: int):
        await tmp_wiki.update_page(
            page.id, content=f"Updated content version {i}"
        )

    await asyncio.gather(*[update_one(i) for i in range(5)])

    # Final state must be valid (not corrupted)
    final = await tmp_wiki.get_page(page.id)
    assert final is not None
    assert final.title == "Shared Page"
    assert final.content  # Some content present


# ---------------------------------------------------------------------------
# Closable protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_wiki):
    """close() should be safe to call multiple times."""
    await tmp_wiki.close()
    await tmp_wiki.close()  # Should not raise
