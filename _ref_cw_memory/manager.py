"""Memory manager for wiki-based storage."""

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import aiofiles

from .wiki import WikiPage, WikiIndex


class MemoryManager:
    """Manages the wiki-based memory system with in-memory caching."""

    def __init__(self, base_path: str = ".claudeworker/memory"):
        self.base_path = Path(base_path).expanduser()
        self.raw_path = self.base_path / "raw"
        self.wiki_path = self.base_path / "wiki"
        self.indices_path = self.base_path / "indices"
        self.output_path = self.base_path / "output"

        # Ensure directories exist
        self._ensure_structure()

        # Load or create indices
        self._index: WikiIndex = self._load_index()

        # In-memory caching for O(1) lookups - eliminates O(n²) file scanning
        self._page_cache: Dict[str, Tuple[WikiPage, float]] = {}  # path -> (page, timestamp)
        self._tag_cache: Dict[str, List[str]] = {}  # tag -> list of paths
        self._backlink_cache: Dict[str, List[str]] = {}  # path -> list of backlinks
        self._cache_ttl: float = 60.0  # Cache TTL in seconds
        self._last_full_scan: float = 0.0

        # Initialize caches from index
        self._initialize_caches()
    
    def _ensure_structure(self) -> None:
        """Create directory structure if needed."""
        # Define all directory paths
        dirs = [
            # Raw storage
            self.raw_path / "tasks",
            self.raw_path / "web",
            self.raw_path / "files",
            self.raw_path / "artifacts",
            # Wiki
            self.wiki_path / "tasks",
            self.wiki_path / "concepts",
            self.wiki_path / "skills",
            self.wiki_path / "sessions",
            # Indices
            self.indices_path,
            # Output
            self.output_path / "reports",
            self.output_path / "slides",
            self.output_path / "visualizations",
            self.output_path / "exports",
        ]
        
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
    
    def _load_index(self) -> WikiIndex:
        """Load or create the wiki index."""
        index_file = self.indices_path / "wiki-index.json"
        if index_file.exists():
            try:
                with open(index_file) as f:
                    data = json.load(f)
                return WikiIndex.model_validate(data)
            except Exception:
                pass
        return WikiIndex()
    
    def _save_index(self) -> None:
        """Save the wiki index."""
        index_file = self.indices_path / "wiki-index.json"
        with open(index_file, "w") as f:
            json.dump(self._index.model_dump(mode="json"), f, indent=2)

    def _initialize_caches(self) -> None:
        """Initialize in-memory caches from the index."""
        # Build tag cache from index
        self._tag_cache = dict(self._index.tags)

        # Build backlink cache from index pages
        for path, data in self._index.pages.items():
            links_to = data.get("links_to", [])
            for target in links_to:
                if target not in self._backlink_cache:
                    self._backlink_cache[target] = []
                if path not in self._backlink_cache[target]:
                    self._backlink_cache[target].append(path)

    def _is_cache_valid(self, path: str) -> bool:
        """Check if a cached page is still valid."""
        if path not in self._page_cache:
            return False
        _, timestamp = self._page_cache[path]
        return (time.time() - timestamp) < self._cache_ttl

    def _get_cached_page(self, path: str) -> Optional[WikiPage]:
        """Get a page from cache if valid."""
        if self._is_cache_valid(path):
            return self._page_cache[path][0]
        return None

    def _set_cached_page(self, path: str, page: WikiPage) -> None:
        """Cache a page with timestamp."""
        self._page_cache[path] = (page, time.time())

    def _invalidate_cache(self, path: str) -> None:
        """Invalidate cache for a specific path."""
        if path in self._page_cache:
            del self._page_cache[path]

    async def _get_page_with_cache(self, path: str) -> Optional[WikiPage]:
        """Get a page using cache - avoids O(n²) file scanning."""
        # Try cache first
        cached = self._get_cached_page(path)
        if cached:
            return cached

        # Load from disk and cache
        page = await self.get_page(path)
        if page:
            self._set_cached_page(path, page)
        return page

    async def _build_backlink_cache(self) -> Dict[str, List[str]]:
        """Build backlink cache - O(n) instead of O(n²) per query."""
        backlink_map: Dict[str, List[str]] = {}

        # Scan all files once
        for wiki_file in self.wiki_path.rglob("*.md"):
            rel_path = wiki_file.relative_to(self.wiki_path)
            path = str(rel_path.with_suffix(""))

            content = wiki_file.read_text()
            page = WikiPage.from_markdown(path, content)

            # Update cache
            self._set_cached_page(path, page)

            # Build backlink map
            for link_target, _ in page.extract_links():
                if link_target not in backlink_map:
                    backlink_map[link_target] = []
                if path not in backlink_map[link_target]:
                    backlink_map[link_target].append(path)

        self._backlink_cache = backlink_map
        self._last_full_scan = time.time()
        return backlink_map

    def _get_cached_backlinks(self, path: str) -> List[str]:
        """Get backlinks from cache."""
        return self._backlink_cache.get(path, [])

    async def warm_cache(self) -> None:
        """Pre-load all pages into memory cache."""
        await self._build_backlink_cache()
        # Tag cache is built from index during init
        self._tag_cache = dict(self._index.tags)

    # ==================== INGEST ====================
    
    async def ingest(
        self,
        source: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        raw_store: bool = True,
        filename: Optional[str] = None,
    ) -> str:
        """Ingest raw data into memory.
        
        Args:
            source: Type of source ("web", "file", "task", "artifact")
            content: The content to store
            metadata: Additional metadata
            raw_store: Whether to store in raw/ directory
            filename: Optional specific filename
            
        Returns:
            Path to the stored raw data
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        if not filename:
            filename = f"{timestamp}-{hash(content) % 10000:04d}.md"
        
        # Map source types to directories
        source_dirs = {
            "web": self.raw_path / "web",
            "file": self.raw_path / "files",
            "artifact": self.raw_path / "artifacts",
        }
        
        if source == "task":
            task_id = metadata.get("task_id", "unknown") if metadata else "unknown"
            raw_dir = self.raw_path / "tasks" / task_id
        else:
            raw_dir = source_dirs.get(source, self.raw_path / source)
        
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / filename
        
        if raw_store:
            # Write with metadata header
            header = f"""---
source: {source}
ingested_at: {timestamp}
metadata: {json.dumps(metadata or {})}
---

"""
            async with aiofiles.open(raw_file, "w") as f:
                await f.write(header + content)
        
        return str(raw_file)
    
    # ==================== COMPILE ====================
    
    async def compile_wiki(
        self,
        path: str,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WikiPage:
        """Compile raw data into a wiki page with cache update.

        Args:
            path: Relative path in wiki/ (e.g., "tasks/my-task")
            title: Page title
            content: Markdown content
            tags: Optional tags
            metadata: Additional metadata

        Returns:
            The created WikiPage
        """
        page = WikiPage(
            path=path,
            title=title,
            content=content,
            tags=tags or [],
            metadata=metadata or {},
            updated_at=datetime.now(timezone.utc),
        )

        # Write to file
        wiki_file = self.wiki_path / f"{path}.md"
        wiki_file.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(wiki_file, "w") as f:
            await f.write(page.to_markdown())

        # Update index
        self._index.add_page(page)
        self._save_index()

        # Update in-memory cache (O(1) instead of O(n²))
        self._set_cached_page(path, page)

        # Update tag cache
        for tag in page.tags:
            if tag not in self._tag_cache:
                self._tag_cache[tag] = []
            if path not in self._tag_cache[tag]:
                self._tag_cache[tag].append(path)

        # Update backlink cache for linked pages
        for link_target, _ in page.extract_links():
            if link_target not in self._backlink_cache:
                self._backlink_cache[link_target] = []
            if path not in self._backlink_cache[link_target]:
                self._backlink_cache[link_target].append(path)

        return page
    
    async def update_backlinks(self) -> None:
        """Update backlinks for all wiki pages."""
        # First pass: collect all links
        all_links: Dict[str, List[str]] = {}  # target -> list of sources
        
        for wiki_file in self.wiki_path.rglob("*.md"):
            rel_path = wiki_file.relative_to(self.wiki_path)
            path = str(rel_path.with_suffix(""))
            
            content = wiki_file.read_text()
            page = WikiPage.from_markdown(path, content)
            
            for link_target, _ in page.extract_links():
                if link_target not in all_links:
                    all_links[link_target] = []
                if path not in all_links[link_target]:
                    all_links[link_target].append(path)
        
        # Second pass: update backlinks
        for wiki_file in self.wiki_path.rglob("*.md"):
            rel_path = wiki_file.relative_to(self.wiki_path)
            path = str(rel_path.with_suffix(""))
            
            content = wiki_file.read_text()
            page = WikiPage.from_markdown(path, content)
            
            # Update backlinks
            page_key = page.title.lower().replace(" ", "-")
            page.backlinks = all_links.get(page_key, [])
            page.backlinks.extend(all_links.get(path, []))
            page.backlinks = list(set(page.backlinks))  # Deduplicate
            
            # Rewrite file
            async with aiofiles.open(wiki_file, "w") as f:
                await f.write(page.to_markdown())
    
    # ==================== QUERY ====================
    
    async def query(
        self,
        query: str,
        strategy: str = "index-first",
        limit: int = 5,
    ) -> List[WikiPage]:
        """Query the wiki for relevant pages using in-memory caching.

        Args:
            query: Search query
            strategy: Search strategy ("index-first", "full-text")
            limit: Maximum results

        Returns:
            List of matching wiki pages
        """
        results = []

        if strategy == "index-first":
            # Search the index first (O(1) index lookup)
            indexed = self._index.search(query)

            for path, score in indexed[:limit]:
                # Use cache to avoid file I/O
                page = await self._get_page_with_cache(path)
                if page:
                    results.append(page)

        elif strategy == "full-text":
            # Optimized full-text search using cache
            query_lower = query.lower()

            # Ensure cache is warmed up for full-text search
            if time.time() - self._last_full_scan > self._cache_ttl:
                await self._build_backlink_cache()

            # Search cached pages instead of reading files
            for path, (page, _) in self._page_cache.items():
                if len(results) >= limit:
                    break

                if query_lower in page.content.lower() or query_lower in page.title.lower():
                    results.append(page)

            # If cache doesn't have enough results, fall back to file search
            if len(results) < limit:
                searched_paths = {p.path for p in results}
                for wiki_file in self.wiki_path.rglob("*.md"):
                    if len(results) >= limit:
                        break

                    rel_path = wiki_file.relative_to(self.wiki_path)
                    path = str(rel_path.with_suffix(""))

                    if path in searched_paths:
                        continue

                    content = wiki_file.read_text()
                    if query_lower in content.lower():
                        page = WikiPage.from_markdown(path, content)
                        self._set_cached_page(path, page)
                        results.append(page)

        return results
    
    async def get_page(self, path: str) -> Optional[WikiPage]:
        """Get a specific wiki page by path."""
        wiki_file = self.wiki_path / f"{path}.md"
        if wiki_file.exists():
            content = wiki_file.read_text()
            return WikiPage.from_markdown(path, content)
        return None
    
    # ==================== OUTPUT ====================
    
    async def output(
        self,
        format: str,
        content: str,
        filename: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate output artifact.
        
        Args:
            format: Output format ("markdown", "html", "csv", "json")
            content: The content to write
            filename: Output filename
            metadata: Additional metadata
            
        Returns:
            Path to the output file
        """
        # Map formats to output directories
        format_dirs = {
            "markdown": self.output_path / "reports",
            "md": self.output_path / "reports",
            "slides": self.output_path / "slides",
            "png": self.output_path / "visualizations",
            "jpg": self.output_path / "visualizations",
            "svg": self.output_path / "visualizations",
        }
        output_dir = format_dirs.get(format, self.output_path / "exports")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / filename
        
        async with aiofiles.open(output_file, "w") as f:
            await f.write(content)
        
        return str(output_file)
    
    # ==================== LINT ====================
    
    async def lint(self) -> Dict[str, Any]:
        """Check wiki health and return issues using cached data.

        Returns:
            Dictionary with linting results
        """
        issues = {
            "orphaned_pages": [],
            "broken_links": [],
            "empty_pages": [],
            "suggestions": [],
        }

        # Use cached data if available, otherwise build cache once
        if time.time() - self._last_full_scan > self._cache_ttl:
            await self._build_backlink_cache()

        all_pages = set(self._page_cache.keys())

        # Check cached pages for issues
        all_links: set[tuple[str, str]] = set()

        for path, (page, _) in self._page_cache.items():
            # Check for empty pages
            if not page.content.strip():
                issues["empty_pages"].append(path)

            # Collect links
            for link_target, _ in page.extract_links():
                all_links.add((path, link_target))

        # Check for broken links using cached page set
        for source, target in all_links:
            target_path = target.replace(" ", "-").lower()
            if target_path not in all_pages and target not in all_pages:
                issues["broken_links"].append({"from": source, "to": target})

        # Check for orphaned pages using backlink cache (O(1) lookup)
        for path in all_pages:
            backlinks = self._get_cached_backlinks(path)
            if not backlinks and path != "index":
                issues["orphaned_pages"].append(path)

        return issues
    
    # ==================== UTILITY ====================
    
    def get_raw_path(self, *parts: str) -> Path:
        """Get a path in the raw directory."""
        return self.raw_path.joinpath(*parts)
    
    def get_wiki_path(self, *parts: str) -> Path:
        """Get a path in the wiki directory."""
        return self.wiki_path.joinpath(*parts)
    
    def get_output_path(self, *parts: str) -> Path:
        """Get a path in the output directory."""
        return self.output_path.joinpath(*parts)
