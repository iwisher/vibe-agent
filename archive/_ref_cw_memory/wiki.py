"""Wiki page and index models."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class WikiLink:
    """Wiki-style link [[page|title]]."""
    
    WIKI_LINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')
    
    @classmethod
    def extract(cls, content: str) -> List[tuple[str, Optional[str]]]:
        """Extract all wiki links from content."""
        return [(m.group(1), m.group(2)) for m in cls.WIKI_LINK_PATTERN.finditer(content)]
    
    @classmethod
    def create(cls, page: str, title: Optional[str] = None) -> str:
        """Create a wiki link."""
        if title:
            return f"[[{page}|{title}]]"
        return f"[[{page}]]"


class WikiPage(BaseModel):
    """A wiki page in the memory system."""
    
    model_config = ConfigDict(use_enum_values=True)
    
    path: str  # Relative path in wiki/
    title: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: List[str] = Field(default_factory=list)
    backlinks: List[str] = Field(default_factory=list)  # Pages that link to this one
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def filename(self) -> str:
        """Get the filename for this page."""
        # Convert title to safe filename
        safe = re.sub(r'[^\w\s-]', '', self.title).strip().lower()
        safe = re.sub(r'[-\s]+', '-', safe)
        return f"{safe}.md"
    
    def extract_links(self) -> List[tuple[str, Optional[str]]]:
        """Extract all wiki links from content."""
        return WikiLink.extract(self.content)
    
    def to_markdown(self) -> str:
        """Convert to markdown format."""
        lines = [
            f"# {self.title}",
            "",
            f"_Created: {self.created_at.isoformat()}_",
            f"_Updated: {self.updated_at.isoformat()}_",
            "",
        ]
        
        if self.tags:
            lines.extend([
                f"**Tags:** {', '.join(self.tags)}",
                "",
            ])
        
        lines.extend([
            self.content,
            "",
        ])
        
        if self.backlinks:
            lines.extend([
                "## Backlinks",
                "",
            ])
            for link in self.backlinks:
                lines.append(f"- [[{link}]]")
            lines.append("")
        
        return "\n".join(lines)
    
    @classmethod
    def from_markdown(cls, path: str, content: str) -> "WikiPage":
        """Parse a markdown file into a WikiPage."""
        lines = content.split('\n')
        
        # Extract title from first h1
        title = "Untitled"
        if lines and lines[0].startswith('# '):
            title = lines[0][2:].strip()
        
        # Extract metadata from frontmatter or inline
        tags = []
        backlinks = []
        metadata = {}
        
        # Look for tags line
        for line in lines:
            if line.startswith('**Tags:**'):
                tag_str = line[9:].strip()
                tags = [t.strip() for t in tag_str.split(',')]
            
            # Extract backlinks section
            if line == "## Backlinks":
                idx = lines.index(line) + 2
                while idx < len(lines) and lines[idx].startswith('- '):
                    link_text = lines[idx][2:].strip()
                    # Extract [[link]] format
                    match = re.search(r'\[\[([^\]]+)\]\]', link_text)
                    if match:
                        backlinks.append(match.group(1))
                    idx += 1
        
        # Main content is everything after title/metadata
        content_start = 1
        while content_start < len(lines) and (
            lines[content_start].startswith('_') or
            lines[content_start].startswith('**') or
            lines[content_start] == ""
        ):
            content_start += 1
        
        # Find end of content (before backlinks)
        content_end = len(lines)
        for i, line in enumerate(lines):
            if line == "## Backlinks":
                content_end = i
                break
        
        main_content = '\n'.join(lines[content_start:content_end]).strip()
        
        return cls(
            path=path,
            title=title,
            content=main_content,
            tags=tags,
            backlinks=backlinks,
            metadata=metadata,
        )


class WikiIndex(BaseModel):
    """Index for fast wiki lookups."""
    
    model_config = ConfigDict(use_enum_values=True)
    
    version: int = 1
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pages: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # path -> metadata
    tags: Dict[str, List[str]] = Field(default_factory=dict)  # tag -> list of page paths
    concepts: Dict[str, List[str]] = Field(default_factory=dict)  # concept -> related pages
    
    def add_page(self, page: WikiPage) -> None:
        """Add a page to the index."""
        self.pages[page.path] = {
            "title": page.title,
            "created_at": page.created_at.isoformat(),
            "updated_at": page.updated_at.isoformat(),
            "tags": page.tags,
            "links_to": [link[0] for link in page.extract_links()],
        }
        
        # Update tag index
        for tag in page.tags:
            if tag not in self.tags:
                self.tags[tag] = []
            if page.path not in self.tags[tag]:
                self.tags[tag].append(page.path)
    
    def remove_page(self, path: str) -> None:
        """Remove a page from the index."""
        if path in self.pages:
            page_data = self.pages.pop(path)
            # Remove from tag index
            for tag in page_data.get("tags", []):
                if tag in self.tags and path in self.tags[tag]:
                    self.tags[tag].remove(path)
    
    def search_by_tag(self, tag: str) -> List[str]:
        """Get pages by tag."""
        return self.tags.get(tag, [])
    
    def search(self, query: str) -> List[tuple[str, float]]:
        """Simple text search over page titles."""
        query_lower = query.lower()
        results = []
        
        for path, data in self.pages.items():
            title = data.get("title", "").lower()
            if query_lower in title:
                # Simple scoring: exact match = 1.0, contains = 0.5
                score = 1.0 if query_lower == title else 0.5
                results.append((path, score))
        
        return sorted(results, key=lambda x: x[1], reverse=True)
