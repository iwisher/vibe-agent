"""Wiki graph database and entity resolution.

Provides graph-based relationships between wiki pages, enabling:
- Entity resolution (same concept, different names)
- Relationship traversal (related pages, parent/child)
- Graph-based search and recommendation
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EntityNode:
    """A node in the wiki knowledge graph representing an entity."""

    entity_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    page_ids: list[str] = field(default_factory=list)
    entity_type: str = "concept"  # concept, person, place, organization, etc.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityEdge:
    """An edge between two entities in the knowledge graph."""

    source_id: str
    target_id: str
    relation: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


class WikiGraph:
    """Graph database for wiki entity relationships.

    Stores entities and their relationships, enabling:
    - Entity resolution (find all pages about the same concept)
    - Relationship traversal (find related concepts)
    - Graph-based recommendations
    """

    def __init__(self, graph_path: str | Path | None = None) -> None:
        self.graph_path = Path(graph_path).expanduser() if graph_path else None
        self._entities: dict[str, EntityNode] = {}
        self._edges: list[EntityEdge] = []
        self._alias_map: dict[str, str] = {}  # alias -> entity_id
        self._page_to_entity: dict[str, str] = {}  # page_id -> entity_id

    def add_entity(self, entity: EntityNode) -> None:
        """Add an entity to the graph."""
        self._entities[entity.entity_id] = entity
        # Index aliases
        for alias in entity.aliases:
            self._alias_map[alias.lower()] = entity.entity_id
        # Index page mappings
        for page_id in entity.page_ids:
            self._page_to_entity[page_id] = entity.entity_id

    def add_edge(self, edge: EntityEdge) -> None:
        """Add a relationship between two entities."""
        if edge.source_id not in self._entities or edge.target_id not in self._entities:
            logger.warning(f"Cannot add edge: missing entity {edge.source_id} or {edge.target_id}")
            return
        self._edges.append(edge)

    def resolve_entity(self, name: str) -> EntityNode | None:
        """Resolve a name/alias to an entity."""
        name_lower = name.lower()
        # Direct match
        for entity in self._entities.values():
            if entity.name.lower() == name_lower:
                return entity
        # Alias match
        entity_id = self._alias_map.get(name_lower)
        if entity_id:
            return self._entities.get(entity_id)
        return None

    def get_related(
        self, entity_id: str, relation: str | None = None
    ) -> list[tuple[EntityNode, float]]:
        """Get entities related to the given entity.

        Args:
            entity_id: Source entity
            relation: Optional relation type filter

        Returns:
            List of (entity, weight) tuples sorted by weight desc
        """
        results = []
        for edge in self._edges:
            if edge.source_id == entity_id:
                if relation is None or edge.relation == relation:
                    target = self._entities.get(edge.target_id)
                    if target:
                        results.append((target, edge.weight))
            elif edge.target_id == entity_id:
                # Bidirectional: also include reverse
                if relation is None or edge.relation == relation:
                    source = self._entities.get(edge.source_id)
                    if source:
                        results.append((source, edge.weight))

        results.sort(key=lambda x: -x[1])
        return results

    def get_pages_for_entity(self, entity_id: str) -> list[str]:
        """Get all wiki page IDs associated with an entity."""
        entity = self._entities.get(entity_id)
        return entity.page_ids if entity else []

    def find_entity_for_page(self, page_id: str) -> EntityNode | None:
        """Find the entity associated with a wiki page."""
        entity_id = self._page_to_entity.get(page_id)
        return self._entities.get(entity_id) if entity_id else None

    def merge_entities(self, primary_id: str, secondary_id: str) -> bool:
        """Merge two entities into one (entity resolution).

        All aliases, pages, and edges from secondary are moved to primary.
        """
        primary = self._entities.get(primary_id)
        secondary = self._entities.get(secondary_id)
        if not primary or not secondary:
            return False

        # Merge aliases
        for alias in secondary.aliases:
            if alias not in primary.aliases:
                primary.aliases.append(alias)
            self._alias_map[alias.lower()] = primary_id

        # Merge page IDs
        for page_id in secondary.page_ids:
            if page_id not in primary.page_ids:
                primary.page_ids.append(page_id)
            self._page_to_entity[page_id] = primary_id

        # Update edges
        for edge in self._edges:
            if edge.source_id == secondary_id:
                edge.source_id = primary_id
            if edge.target_id == secondary_id:
                edge.target_id = primary_id

        # Remove secondary
        del self._entities[secondary_id]
        return True

    def save(self) -> None:
        """Persist graph to disk."""
        if not self.graph_path:
            return

        data = {
            "entities": {
                eid: {
                    "entity_id": e.entity_id,
                    "name": e.name,
                    "aliases": e.aliases,
                    "page_ids": e.page_ids,
                    "entity_type": e.entity_type,
                    "metadata": e.metadata,
                }
                for eid, e in self._entities.items()
            },
            "edges": [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "relation": e.relation,
                    "weight": e.weight,
                    "metadata": e.metadata,
                }
                for e in self._edges
            ],
        }

        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def load(self) -> None:
        """Load graph from disk."""
        if not self.graph_path or not self.graph_path.exists():
            return

        try:
            data = json.loads(self.graph_path.read_text())
            self._entities.clear()
            self._edges.clear()
            self._alias_map.clear()
            self._page_to_entity.clear()

            for eid, edata in data.get("entities", {}).items():
                entity = EntityNode(**edata)
                self.add_entity(entity)

            for edata in data.get("edges", []):
                edge = EntityEdge(**edata)
                self.add_edge(edge)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to load wiki graph: {e}")

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def list_entities(self, entity_type: str | None = None) -> list[EntityNode]:
        """List all entities, optionally filtered by type."""
        entities = list(self._entities.values())
        if entity_type:
            entities = [e for e in entities if e.entity_type == entity_type]
        return sorted(entities, key=lambda e: e.name)

    def suggest_merge_candidates(
        self, similarity_threshold: float = 0.8
    ) -> list[tuple[str, str, float]]:
        """Suggest entity pairs that might be duplicates.

        Returns list of (entity_id_1, entity_id_2, similarity) tuples.
        """
        candidates = []
        entity_list = list(self._entities.values())

        for i, e1 in enumerate(entity_list):
            for e2 in entity_list[i + 1 :]:
                # Check name similarity
                names1 = {e1.name.lower()} | set(a.lower() for a in e1.aliases)
                names2 = {e2.name.lower()} | set(a.lower() for a in e2.aliases)
                overlap = len(names1 & names2)
                union = len(names1 | names2)
                if union > 0:
                    sim = overlap / union
                    if sim >= similarity_threshold:
                        candidates.append((e1.entity_id, e2.entity_id, sim))

        candidates.sort(key=lambda x: -x[2])
        return candidates
