"""Hybrid Semantic Planner for tool, skill, and MCP selection.

Four-tier planner:
1. Keyword fast-path (existing, free)
2. fastText embedding scorer (5MB model, local)
3. LLM router (cheap model, JSON output)
4. Return all tools (safety fallback)
"""

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from vibe.harness.instructions import Skill

# Optional dependencies — imported at module level with graceful fallback
try:
    import fasttext
except ImportError:
    fasttext = None  # type: ignore

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore


@dataclass
class PlanRequest:
    query: str
    available_tools: list[dict[str, Any]] = field(default_factory=list)
    available_skills: list[Skill] = field(default_factory=list)
    available_mcps: list[dict[str, Any]] = field(default_factory=list)
    history_summary: str = ""
    wiki_hint: str = ""  # v4: injected by QueryLoop.run() from PageIndex, not by planner itself


@dataclass
class PlanResult:
    selected_tool_names: list[str] = field(default_factory=list)
    selected_skills: list[Skill] = field(default_factory=list)
    selected_mcps: list[dict[str, Any]] = field(default_factory=list)
    system_prompt_append: str = ""
    reasoning: str = ""
    planner_tier: str = "unknown"  # keyword | embedding | llm | fallback


class HybridPlanner:
    """Hybrid planner with keyword, embedding, and LLM tiers.

    Tier 1: Keyword fast-path (free, deterministic)
    Tier 2: fastText embedding scorer (5MB model, local)
    Tier 3: LLM router (cheap model, JSON output)
    Tier 4: Return all tools (safety fallback, never starve)
    """

    # Thresholds
    KEYWORD_THRESHOLD = 1  # Minimum keyword score to trigger fast-path
    EMBEDDING_HIGH_CONFIDENCE = 0.8  # Skip LLM if embedding similarity > this
    EMBEDDING_MIN_SIMILARITY = 0.75  # Below this, go straight to LLM
    MAX_LLM_TOOLS = 10  # Don't overwhelm LLM router with too many tools

    def __init__(
        self,
        trace_store: Any | None = None,
        embedding_model_path: Optional[str] = None,
        llm_client: Any | None = None,
        cache_dir: Optional[Path] = None,
        *,
        pageindex: Any | None = None,  # v4: keyword-only, preserves positional compat
    ):
        self.trace_store = trace_store
        self.llm_client = llm_client
        self.pageindex = pageindex  # kept for reference; routing done in QueryLoop.run()
        self._embedding_model = None  # kept for test compat; not used directly

        # Setup cache directory
        if cache_dir is None:
            cache_dir = Path.home() / ".vibe" / "planner_cache"
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize fastText if available
        self._init_fasttext(embedding_model_path)

        # Query cache: LRU with TTL
        self._query_cache: dict[str, tuple[PlanResult, float]] = {}  # result, timestamp
        self._query_cache_ttl = 3600  # 1 hour

    def _init_fasttext(self, model_path: Optional[str]) -> None:
        """Initialize embedding model via shared module (singleton)."""
        from vibe.harness.embeddings import load_model
        self._embedding_model = load_model(model_path)
        if self._embedding_model is None:
            pass  # Silent fallback — keyword tier will handle

    def _get_embedding(self, text: str) -> list[float]:
        """Get embedding vector for text via shared module."""
        from vibe.harness.embeddings import get_embedding
        result = get_embedding(text)
        return result if result is not None else []

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors via shared module."""
        from vibe.harness.embeddings import cosine_similarity
        return cosine_similarity(a, b)

    def _check_query_cache(self, request: PlanRequest) -> Optional[PlanResult]:
        """Check if we have a cached result for this query."""
        # Build cache key from query + tool set hash
        tool_names = sorted([t.get("name", "") for t in request.available_tools])
        cache_key = hashlib.md5(
            f"{request.query}:{','.join(tool_names)}".encode()
        ).hexdigest()

        if cache_key in self._query_cache:
            result, timestamp = self._query_cache[cache_key]
            if time.time() - timestamp < self._query_cache_ttl:
                return result

        return None

    def _cache_result(self, request: PlanRequest, result: PlanResult) -> None:
        """Cache planner result."""
        tool_names = sorted([t.get("name", "") for t in request.available_tools])
        cache_key = hashlib.md5(
            f"{request.query}:{','.join(tool_names)}".encode()
        ).hexdigest()

        self._query_cache[cache_key] = (result, time.time())

        # LRU eviction - keep only 100 entries
        if len(self._query_cache) > 100:
            oldest = min(self._query_cache.items(), key=lambda x: x[1][1])
            del self._query_cache[oldest[0]]

    def plan(self, request: PlanRequest) -> PlanResult:
        """Plan with four-tier approach."""
        # Check cache first
        cached = self._check_query_cache(request)
        if cached:
            cached.reasoning += " (cached)"
            return cached

        # Tier 1: Keyword fast-path
        keyword_result = self._keyword_plan(request)
        if keyword_result and (keyword_result.selected_tool_names or keyword_result.selected_skills or keyword_result.selected_mcps):
            keyword_result.planner_tier = "keyword"
            self._cache_result(request, keyword_result)
            return keyword_result

        # Tier 2: Embedding scorer
        embedding_result = self._embedding_plan(request)
        if embedding_result:
            max_sim = getattr(embedding_result, '_max_similarity', 0.0)

            # High-confidence fast-path
            if max_sim >= self.EMBEDDING_HIGH_CONFIDENCE:
                embedding_result.planner_tier = "embedding_high_confidence"
                self._cache_result(request, embedding_result)
                return embedding_result

            # Low similarity - skip to LLM
            if max_sim < self.EMBEDDING_MIN_SIMILARITY:
                pass  # Fall through to LLM
            else:
                # Medium confidence - use embedding result but mark it
                embedding_result.planner_tier = "embedding"
                self._cache_result(request, embedding_result)
                return embedding_result

        # Tier 3: LLM router
        if self.llm_client is not None and request.available_tools:
            llm_result = self._llm_plan(request)
            if llm_result and llm_result.selected_tool_names:
                llm_result.planner_tier = "llm"
                self._cache_result(request, llm_result)
                return llm_result

        # Tier 4: Safety fallback - return all tools
        fallback_result = PlanResult(
            selected_tool_names=[t.get("name", "") for t in request.available_tools],
            selected_skills=request.available_skills,
            selected_mcps=request.available_mcps,
            system_prompt_append="",
            reasoning="Safety fallback: returning all tools (no planner match)",
            planner_tier="fallback",
        )
        self._cache_result(request, fallback_result)
        return fallback_result

    def _keyword_plan(self, request: PlanRequest) -> Optional[PlanResult]:
        """Tier 1: Keyword-based planning (existing logic)."""
        selected_tools = self._select_tools(request.query, request.available_tools)
        selected_skills = self._match_skills(request.query, request.available_skills)
        selected_mcps = self._select_mcps(request.query, request.available_mcps)

        # Only return if we have meaningful matches
        if not selected_tools and not selected_skills and not selected_mcps:
            return None

        # Build system prompt append
        prompt_parts = []
        if selected_skills:
            prompt_parts.append("## Relevant Skills")
            for skill in selected_skills:
                prompt_parts.append(f"### {skill.name}\n{skill.description}\n{skill.content}")

        if selected_mcps:
            prompt_parts.append("## Available External Tools (MCP)")
            for mcp in selected_mcps:
                prompt_parts.append(f"- {mcp.get('name', 'unknown')}: {mcp.get('description', '')}")

        # Memory augmentation
        memory_hint = ""
        if self.trace_store is not None:
            similar = self.trace_store.get_similar_sessions(request.query, limit=3)
            if similar:
                memory_hint = "\n\n## Historical Context\nPreviously successful sessions on similar topics used models such as: " + ", ".join(
                    {s.get("model", "unknown") for s in similar if s.get("model")}
                ) + "."

        # v4: Wiki hint comes from PlanRequest.wiki_hint (injected by QueryLoop.run())
        # PageIndex retrieval happens BEFORE planner in async context, NOT here.
        if request.wiki_hint:
            memory_hint += request.wiki_hint

        if memory_hint:
            prompt_parts.append(memory_hint.strip())

        system_prompt_append = "\n\n".join(prompt_parts)

        reasoning_parts = []
        if selected_tools:
            reasoning_parts.append(f"Selected tools: {[t.get('name') for t in selected_tools]}")
        if selected_skills:
            reasoning_parts.append(f"Selected skills: {[s.name for s in selected_skills]}")
        if selected_mcps:
            reasoning_parts.append(f"Selected MCPs: {[m.get('name') for m in selected_mcps]}")
        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "Keyword match."

        return PlanResult(
            selected_tool_names=[t.get("name", "") for t in selected_tools],
            selected_skills=selected_skills,
            selected_mcps=selected_mcps,
            system_prompt_append=system_prompt_append,
            reasoning=reasoning,
        )

    def _embedding_plan(self, request: PlanRequest) -> Optional[PlanResult]:
        """Tier 2: Embedding-based planning."""
        if not request.available_tools:
            return None

        query_emb = self._get_embedding(request.query)
        if not query_emb:
            return None

        scored_tools = []
        max_similarity = 0.0

        for tool in request.available_tools:
            name = tool.get("name", "")
            desc = tool.get("description", "")
            text = f"{name} {desc}"

            tool_emb = self._get_embedding(text)
            if tool_emb:
                sim = self._cosine_similarity(query_emb, tool_emb)
                max_similarity = max(max_similarity, sim)
                scored_tools.append((sim, tool))

        # Sort by similarity and take top matches
        scored_tools.sort(key=lambda x: x[0], reverse=True)
        matched = [t for s, t in scored_tools if s > 0.75]

        if not matched:
            return None

        # Also match skills and MCPs
        selected_skills = self._match_skills_embedding(request.query, request.available_skills)
        selected_mcps = self._match_mcps_embedding(request.query, request.available_mcps)

        result = PlanResult(
            selected_tool_names=[t.get("name", "") for t in matched],
            selected_skills=selected_skills,
            selected_mcps=selected_mcps,
            system_prompt_append="",
            reasoning=f"Embedding match (max similarity: {max_similarity:.3f})",
        )
        result._max_similarity = max_similarity  # type: ignore
        return result

    def _llm_plan(self, request: PlanRequest) -> Optional[PlanResult]:
        """Tier 3: LLM-based tool selection."""
        if not self.llm_client or not request.available_tools:
            return None

        # Build minimal prompt
        tools_subset = request.available_tools[:self.MAX_LLM_TOOLS]
        tool_list = "\n".join([
            f"- {t.get('name', '')}: {t.get('description', '')}"
            for t in tools_subset
        ])

        prompt = f"""Select the most relevant tools for this query.

Query: {request.query}

Available tools:
{tool_list}

Respond in JSON format:
{{"selected_tools": ["tool_name1", "tool_name2"]}}

Only select tools that are clearly relevant. Return empty array if none match."""

        try:
            response = self.llm_client.complete(prompt)
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                selected_names = parsed.get("selected_tools", [])

                # Map names back to tools
                name_to_tool = {t.get("name", ""): t for t in request.available_tools}
                selected_tools = [name_to_tool[n] for n in selected_names if n in name_to_tool]

                if selected_tools:
                    return PlanResult(
                        selected_tool_names=[t.get("name", "") for t in selected_tools],
                        selected_skills=[],
                        selected_mcps=[],
                        system_prompt_append="",
                        reasoning="LLM router selected tools",
                    )
        except Exception:
            pass

        return None

    # --- Keyword matching methods (from original planner) ---

    @staticmethod
    def _score_text(query: str, text: str) -> int:
        query_lower = query.lower()
        text_lower = text.lower()
        score = 0
        if any(word in text_lower for word in query_lower.split() if len(word) > 2):
            score += 1
        if query_lower in text_lower:
            score += 2
        return score

    def _select_tools(self, query: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored = []
        for tool in tools:
            score = 0
            if isinstance(tool, dict):
                func = tool.get("function", {})
                name = func.get("name", "") if func else tool.get("name", "")
                desc = func.get("description", "") if func else tool.get("description", "")
            else:
                name = getattr(tool, "name", "")
                desc = getattr(tool, "description", "")
            score += self._score_text(query, name) * 2
            score += self._score_text(query, desc)
            scored.append((score, tool))

        matched = [t for s, t in scored if s > 0]
        return matched

    def _match_skills(self, query: str, skills: list[Skill]) -> list[Skill]:
        scored = []
        for skill in skills:
            score = 0
            score += self._score_text(query, skill.name) * 3
            score += self._score_text(query, skill.description) * 2
            score += self._score_text(query, skill.content)
            for tag in skill.tags:
                score += self._score_text(query, tag) * 2
            scored.append((score, skill))

        return [s for sc, s in scored if sc > 0]

    def _select_mcps(self, query: str, mcps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored = []
        for mcp in mcps:
            score = 0
            name = mcp.get("name", "")
            desc = mcp.get("description", "")
            score += self._score_text(query, name) * 2
            score += self._score_text(query, desc)
            scored.append((score, mcp))

        return [m for s, m in scored if s > 0]

    # --- Embedding matching methods ---

    def _match_skills_embedding(self, query: str, skills: list[Skill]) -> list[Skill]:
        query_emb = self._get_embedding(query)
        if not query_emb:
            return []

        scored = []
        for skill in skills:
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)}"
            skill_emb = self._get_embedding(text)
            if skill_emb:
                sim = self._cosine_similarity(query_emb, skill_emb)
                if sim > 0.75:
                    scored.append((sim, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:5]]

    def _match_mcps_embedding(self, query: str, mcps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_emb = self._get_embedding(query)
        if not query_emb:
            return []

        scored = []
        for mcp in mcps:
            text = f"{mcp.get('name', '')} {mcp.get('description', '')}"
            mcp_emb = self._get_embedding(text)
            if mcp_emb:
                sim = self._cosine_similarity(query_emb, mcp_emb)
                if sim > 0.75:
                    scored.append((sim, mcp))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:5]]
