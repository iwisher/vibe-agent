"""Unit tests for contradiction detection in wiki quality gates.

Covers: update_page with contradiction detected → status drops to draft,
update_page without contradiction → status promoted to verified,
flash client unavailable → normal behavior.
"""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.memory.models import WikiPage
from vibe.memory.wiki import LLMWiki


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_wiki(tmp_path):
    return LLMWiki(base_path=tmp_path / "wiki")


@pytest.fixture
def flash_client():
    client = MagicMock()
    client.detect_contradiction = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_page_with_contradiction_drops_to_draft(tmp_wiki):
    """If flash client detects contradiction, status should drop to draft."""
    flash = MagicMock()
    flash.detect_contradiction = AsyncMock(return_value=True)
    tmp_wiki.set_flash_client(flash)

    # Create a backlinking page first so backlinks exist
    page_a = await tmp_wiki.create_page(
        title="Page A", content="Original fact: X is true.", tags=["test"]
    )
    # Create page B that links to page A
    page_b = await tmp_wiki.create_page(
        title="Page B", content="See [[page-a]] for details.", tags=["test"]
    )

    # Update page A with contradictory content
    updated = await tmp_wiki.update_page(
        page_a.id, content="Contradiction: X is now false."
    )
    assert updated.status == "draft"
    # Should have contradiction flag in citations
    flag_citations = [c for c in updated.citations if c.get("type") == "contradiction_flag"]
    assert len(flag_citations) == 1


@pytest.mark.asyncio
async def test_update_page_without_contradiction_promotes_to_verified(tmp_wiki):
    """If no contradiction and page has distinct sessions, promote to verified."""
    flash = MagicMock()
    flash.detect_contradiction = AsyncMock(return_value=False)
    tmp_wiki.set_flash_client(flash)

    page = await tmp_wiki.create_page(
        title="Consistent Page", 
        content="Fact: Y is true.", 
        tags=["test"],
        citations=[{"session": "sess-001", "message_index": 0}]
    )
    # Add a second citation from a different session to meet verified criteria
    updated = await tmp_wiki.update_page(
        page.id,
        content="Fact: Y is true. Additional detail.",
        citations=[{"session": "sess-002", "message_index": 1}],
    )
    # After update with 2 distinct sessions, status should be verified
    assert updated.status == "verified"


@pytest.mark.asyncio
async def test_update_page_with_flash_unavailable(tmp_wiki):
    """If flash client is not set, update should proceed normally."""
    page = await tmp_wiki.create_page(
        title="No Flash Page", content="Fact: Z is true.", tags=["test"]
    )
    updated = await tmp_wiki.update_page(
        page.id, content="Updated fact: Z is still true."
    )
    # Status stays draft because only 1 session citation
    assert updated.status == "draft"
    assert updated.content == "Updated fact: Z is still true."


@pytest.mark.asyncio
async def test_update_page_flash_client_exception(tmp_wiki):
    """If flash client raises, update should proceed normally (non-fatal)."""
    flash = MagicMock()
    flash.detect_contradiction = AsyncMock(side_effect=RuntimeError("Flash model down"))
    tmp_wiki.set_flash_client(flash)

    page = await tmp_wiki.create_page(
        title="Flash Error Page", content="Fact: W is true.", tags=["test"]
    )
    updated = await tmp_wiki.update_page(
        page.id, content="Updated fact: W is still true."
    )
    # Should not crash; status stays draft (only 1 citation)
    assert updated.status == "draft"
    assert updated.content == "Updated fact: W is still true."


@pytest.mark.asyncio
async def test_update_page_no_backlinks_skips_contradiction_check(tmp_wiki):
    """If no pages link to this page, contradiction check should be skipped."""
    flash = MagicMock()
    flash.detect_contradiction = AsyncMock(return_value=False)
    tmp_wiki.set_flash_client(flash)

    page = await tmp_wiki.create_page(
        title="Orphan Page", content="Standalone fact.", tags=["test"]
    )
    updated = await tmp_wiki.update_page(
        page.id, content="Updated standalone fact."
    )
    # detect_contradiction should NOT be called because no backlinks
    flash.detect_contradiction.assert_not_called()
    assert updated.status == "draft"
