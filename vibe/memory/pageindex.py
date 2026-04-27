"""PageIndex Routing Layer — Tripartite Memory System (Phase 1a).

Implements vectorless, reasoning-based RAG via a JSON "Table of Contents":
- LLM reads the JSON index tree and reasons over it to route queries
- Deterministic tag-based hierarchical partitioning (lexicographic sort)
- Async route() with timeout guard (default 2s)
- Sub-index support for hierarchical knowledge bases
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from vibe.memory.models import IndexNode

logger = logging.getLogger(__name__)


class PageIndex:
    """Reasoning-based wiki routing via a JSON index tree.

    Routes queries by having an LLM reason over a human-readable JSON
    "Table of Contents" — no vector embeddings required.
    """

    def __init__(
        self,
        index_path: str | Path,
        llm_client: Any | None = None,
        max_nodes_per_index: int = 100,
        token_threshold: int = 4000,
        routing_timeout_seconds: float = 2.0,
    ) -> None:
        self.index_path = Path(index_path).expanduser()
        self.llm_client = llm_client
        self.max_nodes_per_index = max_nodes_per_index
        self.token_threshold = token_threshold
        self.routing_timeout_seconds = routing_timeout_seconds

        # Root structure
        self._root: IndexNode | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self) -> IndexNode:
        """Load the index from disk. Returns root node."""
        if self._loaded and self._root is not None:
            return self._root

        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                raw_root = data.get("wiki_index", data)
                self._root = IndexNode.from_dict(raw_root)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load index from %s: %s", self.index_path, e)
                self._root = self._empty_root()
        else:
            self._root = self._empty_root()

        self._loaded = True
        return self._root

    def _save(self, root: IndexNode | None = None) -> None:
        """Persist the index tree to disk."""
        node = root or self._root
        if node is None:
            return
        data = {"wiki_index": node.to_dict()}
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _empty_root(self) -> IndexNode:
        return IndexNode(
            node_id="root_01",
            title="Master Knowledge Base",
            description="Top-level index for all agent knowledge.",
        )

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def add_node(
        self,
        parent_id: str,
        title: str,
        description: str,
        file_path: str | None = None,
        tags: list[str] | None = None,
    ) -> IndexNode:
        """Add a new leaf node under parent_id."""
        root = self.load()
        parent = self._find_node(root, parent_id)
        if parent is None:
            parent = root  # fallback to root if parent not found

        new_node = IndexNode(
            node_id=f"doc_{uuid.uuid4().hex[:8]}",
            title=title,
            description=description,
            file_path=file_path,
            tags=tags or [],
        )
        parent.sub_nodes.append(new_node)
        self._partition_if_needed(root)
        self._save()
        return new_node

    def update_node(self, node_id: str, **fields: Any) -> IndexNode:
        """Update fields on an existing node."""
        root = self.load()
        node = self._find_node(root, node_id)
        if node is None:
            raise KeyError(f"Node not found: {node_id}")
        for k, v in fields.items():
            if hasattr(node, k):
                setattr(node, k, v)
        self._save()
        return node

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and its subtree by node_id."""
        root = self.load()
        removed = self._remove_node_recursive(root, node_id)
        if removed:
            self._save()
        return removed

    def _find_node(self, node: IndexNode, node_id: str) -> IndexNode | None:
        if node.node_id == node_id:
            return node
        for child in node.sub_nodes:
            found = self._find_node(child, node_id)
            if found:
                return found
        return None

    def _remove_node_recursive(self, parent: IndexNode, node_id: str) -> bool:
        for i, child in enumerate(parent.sub_nodes):
            if child.node_id == node_id:
                parent.sub_nodes.pop(i)
                return True
            if self._remove_node_recursive(child, node_id):
                return True
        return False

    def _count_nodes(self, node: IndexNode) -> int:
        return 1 + sum(self._count_nodes(c) for c in node.sub_nodes)

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: 1 token ≈ 4 chars."""
        return max(1, len(text) // 4)

    def _index_as_text(self, root: IndexNode) -> str:
        """Serialize index tree to text for token estimation."""
        return json.dumps(root.to_dict(), indent=2)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(self, query: str) -> list[IndexNode]:
        """Route a query to relevant wiki nodes. Returns ranked nodes with confidence."""
        root = self.load()
        if not root.sub_nodes:
            return []

        if self.llm_client is not None:
            return await self._llm_route(query, root)
        else:
            # Tag/keyword fallback when no LLM client
            return self._keyword_route(query, root)

    async def _llm_route(self, query: str, root: IndexNode) -> list[IndexNode]:
        """Use LLM to reason over index and select relevant nodes."""
        index_text = self._index_as_text(root)
        prompt = f"""You are a knowledge routing system. Given a user query and a knowledge base index,
select the most relevant nodes that likely contain information to answer the query.

User Query: {query}

Knowledge Base Index (JSON):
{index_text}

Return a JSON object with this structure:
{{"selected_nodes": [{{"node_id": "...", "confidence": 0.9, "reason": "..."}}]}}

Select 1-5 most relevant nodes. Confidence should be 0.0-1.0.
Only return the JSON, no other text."""

        try:
            # Support both sync and async LLM clients
            if asyncio.iscoroutinefunction(self.llm_client.complete):
                response = await asyncio.wait_for(
                    self.llm_client.complete(prompt),
                    timeout=self.routing_timeout_seconds,
                )
            else:
                loop = asyncio.get_event_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, self.llm_client.complete, prompt),
                    timeout=self.routing_timeout_seconds,
                )

            content = response.content if hasattr(response, "content") else str(response)
            # Extract JSON from response
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                selected = parsed.get("selected_nodes", [])
                result = []
                for item in selected:
                    nid = item.get("node_id", "")
                    node = self._find_node(root, nid)
                    if node:
                        node_copy = IndexNode(
                            node_id=node.node_id,
                            title=node.title,
                            description=node.description,
                            file_path=node.file_path,
                            tags=node.tags,
                            sub_index_path=node.sub_index_path,
                            confidence=float(item.get("confidence", 0.5)),
                        )
                        result.append(node_copy)
                return sorted(result, key=lambda n: n.confidence, reverse=True)
        except asyncio.TimeoutError:
            logger.debug("PageIndex routing timed out for query: %s", query[:60])
        except Exception as e:
            logger.warning("PageIndex LLM routing failed: %s", e)

        # Fallback to keyword routing
        return self._keyword_route(query, root)

    def _keyword_route(self, query: str, root: IndexNode) -> list[IndexNode]:
        """Simple keyword-based routing fallback (no LLM)."""
        q = query.lower().split()
        scored: list[tuple[float, IndexNode]] = []

        def _score(node: IndexNode) -> float:
            score = 0.0
            text = f"{node.title} {node.description} {' '.join(node.tags)}".lower()
            for word in q:
                if len(word) > 2 and word in text:
                    score += 1.0
            return score

        def _traverse(node: IndexNode) -> None:
            if node.file_path:  # Only score leaf nodes
                s = _score(node)
                if s > 0:
                    node_copy = IndexNode(
                        node_id=node.node_id,
                        title=node.title,
                        description=node.description,
                        file_path=node.file_path,
                        tags=node.tags,
                        confidence=min(1.0, s / max(1, len(q))),
                    )
                    scored.append((s, node_copy))
            for child in node.sub_nodes:
                _traverse(child)

        _traverse(root)
        scored.sort(key=lambda x: x[0], reverse=True)
        return [n for _, n in scored[:5]]

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------

    def rebuild(self, wiki: Any, incremental: bool = True) -> None:
        """Rebuild the index from all wiki pages.

        Args:
            wiki: LLMWiki instance (used to list all pages)
            incremental: If True, only update changed pages (not full rebuild).
                         In Phase 1a, we always do a full rebuild synchronously.
        """
        # In Phase 1a: synchronous full rebuild
        # Get all pages synchronously (run coroutine if needed)
        try:
            loop = asyncio.get_running_loop()
            # We're inside an async context — use run_in_executor for sync
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, wiki.list_pages())
                pages = future.result(timeout=30)
        except RuntimeError:
            # No running event loop
            pages = asyncio.run(wiki.list_pages())

        root = self._empty_root()
        for page in pages:
            node = IndexNode(
                node_id=f"doc_{page.id[:8]}",
                title=page.title,
                description=f"Wiki page: {page.title}. Tags: {', '.join(page.tags)}",
                file_path=str(page.path),
                tags=page.tags,
            )
            root.sub_nodes.append(node)

        self._root = root
        self._partition_if_needed(root)
        self._save()
        logger.info("PageIndex rebuilt with %d pages", len(pages))

    # ------------------------------------------------------------------
    # Partitioning
    # ------------------------------------------------------------------

    def _partition_if_needed(self, root: IndexNode) -> None:
        """Trigger partitioning if thresholds exceeded.

        v4: Deterministic tag-based bucketing (lexicographic sort of first tag).
        """
        node_count = self._count_nodes(root)
        token_count = self._estimate_tokens(self._index_as_text(root))

        if node_count <= self.max_nodes_per_index and token_count <= self.token_threshold:
            return

        logger.info(
            "Partitioning PageIndex: %d nodes, %d tokens", node_count, token_count
        )
        self._do_tag_partition(root)

    def _do_tag_partition(self, root: IndexNode) -> None:
        """Partition leaf nodes into tag-based categories.

        Uses lexicographic sort of first tag for determinism.
        Category nodes are created on root; original leaves become sub_nodes.
        """
        leaf_nodes = [n for n in root.sub_nodes if n.file_path is not None]
        category_nodes = [n for n in root.sub_nodes if n.file_path is None]

        if len(leaf_nodes) <= 1:
            return

        # Group leaves by first tag (sorted lexicographically for determinism)
        buckets: dict[str, list[IndexNode]] = {}
        for node in leaf_nodes:
            first_tag = sorted(node.tags)[0] if node.tags else "general"
            buckets.setdefault(first_tag, []).append(node)

        # Build category nodes for multi-node buckets
        new_category_nodes = []
        for tag_key in sorted(buckets.keys()):
            bucket = buckets[tag_key]
            cat_node = IndexNode(
                node_id=f"cat_{tag_key[:20].replace(' ', '_')}",
                title=tag_key.replace("-", " ").title(),
                description=f"Pages tagged with '{tag_key}' ({len(bucket)} pages)",
                tags=[tag_key],
                sub_nodes=bucket,
            )
            new_category_nodes.append(cat_node)

        root.sub_nodes = category_nodes + new_category_nodes
        logger.debug("Partitioned into %d categories", len(new_category_nodes))
