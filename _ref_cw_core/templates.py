"""Task template storage and management.

Templates allow users to save and reuse task patterns.
Stored as JSON files for simplicity.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class TaskTemplate(BaseModel):
    """A reusable task template."""
    
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    task_description: str = Field(..., min_length=1, max_length=2000)
    context: Dict = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=1, le=10)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    use_count: int = Field(default=0, ge=0)
    
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate template name (alphanumeric, spaces, hyphens, underscores only)."""
        if not re.match(r"^[\w\s\-]+$", v):
            raise ValueError("Name must contain only letters, numbers, spaces, hyphens, and underscores")
        return v.strip()
    
    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        """Normalize tags to lowercase."""
        return [tag.lower().strip() for tag in v if tag.strip()]


class TemplateStorage:
    """JSON file-based template storage."""
    
    def __init__(self, storage_path: str = ".claudeworker/templates"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._index_file = self.storage_path / ".index.json"
        self._templates: Dict[str, TaskTemplate] = {}
        self._load_index()
    
    def _load_index(self) -> None:
        """Load template index from disk."""
        if self._index_file.exists():
            try:
                with open(self._index_file, "r") as f:
                    data = json.load(f)
                    for template_data in data.get("templates", []):
                        try:
                            template = TaskTemplate(**template_data)
                            self._templates[template.id] = template
                        except Exception:
                            # Skip invalid templates
                            pass
            except (json.JSONDecodeError, IOError):
                pass
    
    def _save_index(self) -> None:
        """Save template index to disk."""
        data = {
            "templates": [t.model_dump() for t in self._templates.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(self._index_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def create(self, template: TaskTemplate) -> TaskTemplate:
        """Create a new template."""
        # Check for duplicate names
        if any(t.name.lower() == template.name.lower() for t in self._templates.values()):
            raise ValueError(f"Template with name '{template.name}' already exists")
        
        # Generate new ID if not provided or duplicate
        if not template.id or template.id in self._templates:
            template.id = str(uuid4())[:8]
        
        self._templates[template.id] = template
        self._save_index()
        return template
    
    def get(self, template_id: str) -> Optional[TaskTemplate]:
        """Get a template by ID."""
        return self._templates.get(template_id)
    
    def get_by_name(self, name: str) -> Optional[TaskTemplate]:
        """Get a template by name (case-insensitive)."""
        name_lower = name.lower()
        for template in self._templates.values():
            if template.name.lower() == name_lower:
                return template
        return None
    
    def list_all(self, tag: Optional[str] = None) -> List[TaskTemplate]:
        """List all templates, optionally filtered by tag."""
        templates = list(self._templates.values())
        
        if tag:
            tag_lower = tag.lower()
            templates = [t for t in templates if tag_lower in [tt.lower() for tt in t.tags]]
        
        # Sort by use_count (descending) then by name
        return sorted(templates, key=lambda t: (-t.use_count, t.name.lower()))
    
    def update(self, template_id: str, updates: Dict) -> Optional[TaskTemplate]:
        """Update a template."""
        template = self._templates.get(template_id)
        if not template:
            return None
        
        # Check for name collision if name is being updated
        if "name" in updates:
            new_name = updates["name"]
            for tid, t in self._templates.items():
                if tid != template_id and t.name.lower() == new_name.lower():
                    raise ValueError(f"Template with name '{new_name}' already exists")
        
        # Update fields
        update_data = template.model_dump()
        update_data.update(updates)
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        update_data["id"] = template_id  # Preserve ID
        
        self._templates[template_id] = TaskTemplate(**update_data)
        self._save_index()
        return self._templates[template_id]
    
    def delete(self, template_id: str) -> bool:
        """Delete a template. Returns True if deleted."""
        if template_id in self._templates:
            del self._templates[template_id]
            self._save_index()
            return True
        return False
    
    def increment_use_count(self, template_id: str) -> bool:
        """Increment the use count for a template."""
        if template_id in self._templates:
            self._templates[template_id].use_count += 1
            self._save_index()
            return True
        return False
    
    def search(self, query: str) -> List[TaskTemplate]:
        """Search templates by name or description."""
        query_lower = query.lower()
        results = []
        
        for template in self._templates.values():
            if (query_lower in template.name.lower() or 
                query_lower in template.description.lower() or
                any(query_lower in tag.lower() for tag in template.tags)):
                results.append(template)
        
        return sorted(results, key=lambda t: (-t.use_count, t.name.lower()))
    
    def count(self) -> int:
        """Get total number of templates."""
        return len(self._templates)
    
    def clear(self) -> int:
        """Clear all templates. Returns number deleted."""
        count = len(self._templates)
        self._templates.clear()
        self._save_index()
        return count


# Global storage instance
_storage_instance: Optional[TemplateStorage] = None


def get_template_storage() -> TemplateStorage:
    """Get or create global template storage instance."""
    global _storage_instance
    if _storage_instance is None:
        from .config import TemplateConfig
        config = TemplateConfig.from_env()
        _storage_instance = TemplateStorage(config.storage_path)
    return _storage_instance


def reset_template_storage() -> None:
    """Reset global template storage instance (for testing)."""
    global _storage_instance
    _storage_instance = None
