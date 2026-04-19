"""Minimal wiki memory for cross-session knowledge persistence.

Stores structured facts as markdown files in ~/.vibe/wiki/.
Useful for accumulating domain knowledge, conventions, and preferences
across multiple agent sessions.
"""

import os
from pathlib import Path
from typing import Any


def _wiki_dir() -> Path:
    base = os.environ.get("VIBE_MEMORY_DIR")
    if base:
        return Path(base) / "wiki"
    return Path.home() / ".vibe" / "wiki"


def save_page(title: str, content: str) -> Path:
    """Save a wiki page. Returns the file path."""
    wiki = _wiki_dir()
    wiki.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    path = wiki / f"{safe}.md"
    path.write_text(content, encoding="utf-8")
    return path


def load_page(title: str) -> str | None:
    """Load a wiki page by title. Returns None if not found."""
    wiki = _wiki_dir()
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    path = wiki / f"{safe}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def list_pages() -> list[dict[str, Any]]:
    """List all wiki pages with metadata."""
    wiki = _wiki_dir()
    if not wiki.exists():
        return []
    pages = []
    for f in wiki.glob("*.md"):
        stat = f.stat()
        pages.append({
            "title": f.stem,
            "path": str(f),
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
    return sorted(pages, key=lambda p: p["modified"], reverse=True)


def search_pages(query: str) -> list[dict[str, Any]]:
    """Search wiki pages by content substring."""
    query_lower = query.lower()
    results = []
    for page in list_pages():
        content = Path(page["path"]).read_text(encoding="utf-8").lower()
        if query_lower in content:
            results.append(page)
    return results
