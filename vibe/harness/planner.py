"""Pre-LLM context planner for tool, skill, and MCP selection."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from vibe.harness.instructions import Skill


@dataclass
class PlanRequest:
    query: str
    available_tools: List[Dict[str, Any]] = field(default_factory=list)
    available_skills: List[Skill] = field(default_factory=list)
    available_mcps: List[Dict[str, Any]] = field(default_factory=list)
    history_summary: str = ""


@dataclass
class PlanResult:
    selected_tool_names: List[str] = field(default_factory=list)
    selected_skills: List[Skill] = field(default_factory=list)
    selected_mcps: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt_append: str = ""
    reasoning: str = ""


class ContextPlanner:
    """Lightweight keyword-based planner that selects relevant context before the LLM call."""

    def __init__(self, trace_store: Optional[Any] = None):
        self.trace_store = trace_store

    def plan(self, request: PlanRequest) -> PlanResult:
        selected_tools = self._select_tools(request.query, request.available_tools)
        selected_skills = self._match_skills(request.query, request.available_skills)
        selected_mcps = self._select_mcps(request.query, request.available_mcps)

        # Retrieve similar historical sessions for augmentation
        memory_hint = ""
        if self.trace_store is not None:
            similar = self.trace_store.get_similar_sessions(request.query, limit=3)
            if similar:
                memory_hint = "\n\n## Historical Context\nPreviously successful sessions on similar topics used models such as: " + ", ".join(
                    {s.get("model", "unknown") for s in similar if s.get("model")}
                ) + "."

        prompt_parts = []
        if selected_skills:
            prompt_parts.append("## Relevant Skills")
            for skill in selected_skills:
                prompt_parts.append(f"### {skill.name}\n{skill.description}\n{skill.content}")

        if selected_mcps:
            prompt_parts.append("## Available External Tools (MCP)")
            for mcp in selected_mcps:
                prompt_parts.append(f"- {mcp.get('name', 'unknown')}: {mcp.get('description', '')}")

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
        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "No relevant context matched."

        return PlanResult(
            selected_tool_names=[t.get("name", "") for t in selected_tools],
            selected_skills=selected_skills,
            selected_mcps=selected_mcps,
            system_prompt_append=system_prompt_append,
            reasoning=reasoning,
        )

    @staticmethod
    def _score_text(query: str, text: str) -> int:
        query_lower = query.lower()
        text_lower = text.lower()
        score = 0
        # Whole-word / substring bonus
        if any(word in text_lower for word in query_lower.split() if len(word) > 2):
            score += 1
        # Direct containment bonus
        if query_lower in text_lower:
            score += 2
        return score

    def _select_tools(self, query: str, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        return matched if matched else tools

    def _match_skills(self, query: str, skills: List[Skill]) -> List[Skill]:
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

    def _select_mcps(self, query: str, mcps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scored = []
        for mcp in mcps:
            score = 0
            name = mcp.get("name", "")
            desc = mcp.get("description", "")
            score += self._score_text(query, name) * 2
            score += self._score_text(query, desc)
            scored.append((score, mcp))

        return [m for s, m in scored if s > 0]
