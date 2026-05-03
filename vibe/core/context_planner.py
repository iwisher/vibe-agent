"""Context-aware pre-planner for QueryLoop.

Runs before the main LLM call to:
1. Classify user intent (question, command, creative, analysis)
2. Select relevant tools/skills/MCPs from available inventory
3. Route to appropriate wiki knowledge
4. Estimate token budget and complexity
5. Produce a structured ContextPlan that QueryLoop consumes

This is Phase 3.5: the ContextPlanner is lightweight (no LLM call)
and uses keyword + embedding matching for sub-10ms latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from vibe.harness.planner import HybridPlanner, PlanRequest


class IntentType(Enum):
    """Classified user intent."""

    QUESTION = auto()      # Factual query, needs answer
    COMMAND = auto()       # Direct instruction (run tests, deploy)
    CREATIVE = auto()      # Generate content (code, text, design)
    ANALYSIS = auto()      # Analyze data, review, audit
    CONVERSATION = auto()  # Chat, clarification, small talk
    MULTI_STEP = auto()    # Complex task requiring multiple tools


class ContextPriority(Enum):
    """Priority level for context inclusion."""

    CRITICAL = auto()   # Must include (user query, system prompt)
    HIGH = auto()       # Strongly relevant (selected tools, wiki hints)
    MEDIUM = auto()     # Possibly relevant (history summary)
    LOW = auto()        # Nice to have (telemetry, metadata)


@dataclass
class ContextItem:
    """A piece of context with priority and estimated tokens."""

    source: str           # e.g., "wiki", "tool_schema", "history"
    content: str
    priority: ContextPriority
    estimated_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextPlan:
    """Structured plan produced by ContextPlanner.

    Consumed by QueryLoop to build the final LLM prompt.
    """

    intent: IntentType
    intent_confidence: float  # 0.0-1.0
    selected_tools: list[str] = field(default_factory=list)
    selected_skills: list[str] = field(default_factory=list)
    selected_mcps: list[str] = field(default_factory=list)
    context_items: list[ContextItem] = field(default_factory=list)
    estimated_tokens: int = 0
    complexity_score: float = 0.0  # 0.0-1.0, from ComplexityScorer
    suggested_model_tier: str = "standard"  # free|budget|standard|premium|ultra
    reasoning: str = ""

    @property
    def total_context_tokens(self) -> int:
        return sum(item.estimated_tokens for item in self.context_items)

    def get_items_by_priority(self, priority: ContextPriority) -> list[ContextItem]:
        return [item for item in self.context_items if item.priority == priority]

    def build_system_prompt(self) -> str:
        """Build a system prompt from CRITICAL and HIGH priority context items."""
        parts = []
        for item in self.context_items:
            if item.priority in (ContextPriority.CRITICAL, ContextPriority.HIGH):
                if item.source == "wiki":
                    parts.append(f"## Relevant Knowledge\n{item.content}")
                elif item.source == "tool_schema":
                    parts.append(f"## Available Tools\n{item.content}")
                elif item.source == "skill":
                    parts.append(f"## Skill: {item.metadata.get('name', '')}\n{item.content}")
                else:
                    parts.append(item.content)
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Intent classifier
# ---------------------------------------------------------------------------

class IntentClassifier:
    """Lightweight intent classification using keyword patterns."""

    PATTERNS: dict[IntentType, list[str]] = {
        IntentType.QUESTION: [
            "what", "how", "why", "when", "where", "who", "which",
            "explain", "describe", "tell me", "?",
        ],
        IntentType.COMMAND: [
            "run", "execute", "deploy", "build", "test", "install",
            "start", "stop", "restart", "delete", "create", "update",
            "git ", "pip ", "npm ", "docker ", "kubectl ",
        ],
        IntentType.CREATIVE: [
            "write", "generate", "create", "design", "draft", "compose",
            "code", "script", "implement", "build a", "make a",
        ],
        IntentType.ANALYSIS: [
            "analyze", "review", "audit", "check", "inspect", "evaluate",
            "compare", "benchmark", "profile", "debug", "trace",
        ],
        IntentType.CONVERSATION: [
            "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
            "got it", "i see", "understood", "never mind",
        ],
        IntentType.MULTI_STEP: [
            "and then", "after that", "next", "first", "finally",
            "step by step", "workflow", "pipeline", "sequence",
        ],
    }

    def classify(self, query: str) -> tuple[IntentType, float]:
        """Classify intent and return (intent, confidence).

        Confidence is based on keyword match density.
        """
        query_lower = query.lower()
        scores: dict[IntentType, float] = {}

        for intent, patterns in self.PATTERNS.items():
            matches = sum(1 for p in patterns if p in query_lower)
            # Normalize by pattern count (more patterns = more opportunities)
            scores[intent] = matches / max(len(patterns), 1)

        # Boost MULTI_STEP if multiple COMMAND/CREATIVE/ANALYSIS indicators
        command_score = scores.get(IntentType.COMMAND, 0)
        creative_score = scores.get(IntentType.CREATIVE, 0)
        analysis_score = scores.get(IntentType.ANALYSIS, 0)
        if sum(1 for s in [command_score, creative_score, analysis_score] if s > 0) >= 2:
            scores[IntentType.MULTI_STEP] = max(
                scores.get(IntentType.MULTI_STEP, 0),
                0.5,
            )

        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]

        # Normalize confidence: cap at 1.0, floor at 0.1
        confidence = min(max(best_score * 3, 0.1), 1.0)

        return best_intent, confidence


# ---------------------------------------------------------------------------
# ContextPlanner
# ---------------------------------------------------------------------------

class ContextPlanner:
    """Pre-LLM context planner: intent + tool selection + context assembly.

    Wraps the existing HybridPlanner and adds:
    - Intent classification
    - Context item prioritization
    - Token budget estimation
    - Model tier suggestion
    """

    CHARS_PER_TOKEN = 4

    def __init__(
        self,
        hybrid_planner: HybridPlanner | None = None,
        intent_classifier: IntentClassifier | None = None,
        complexity_scorer: Any | None = None,
    ):
        self.hybrid_planner = hybrid_planner or HybridPlanner()
        self.intent_classifier = intent_classifier or IntentClassifier()
        self.complexity_scorer = complexity_scorer

    def plan(
        self,
        query: str,
        available_tools: list[dict[str, Any]],
        available_skills: list[Any] | None = None,
        available_mcps: list[dict[str, Any]] | None = None,
        wiki_hint: str = "",
        history_summary: str = "",
        session_id: str | None = None,
    ) -> ContextPlan:
        """Produce a ContextPlan for the given query.

        Steps:
        1. Classify intent
        2. Run HybridPlanner for tool/skill/MCP selection
        3. Score complexity (if scorer available)
        4. Assemble context items with priorities
        5. Estimate token budget
        6. Suggest model tier
        """
        # Step 1: Intent classification
        intent, intent_confidence = self.intent_classifier.classify(query)

        # Step 2: HybridPlanner for tool selection
        plan_request = PlanRequest(
            query=query,
            available_tools=available_tools,
            available_skills=available_skills or [],
            available_mcps=available_mcps or [],
            history_summary=history_summary,
            wiki_hint=wiki_hint,
        )
        plan_result = self.hybrid_planner.plan(plan_request)

        # Step 3: Complexity scoring
        complexity_score = 0.0
        if self.complexity_scorer is not None:
            try:
                messages = [{"role": "user", "content": query}]
                tools = [{"name": t.get("function", {}).get("name", t.get("name", ""))} for t in available_tools]
                comp_result = self.complexity_scorer.score(messages, tools)
                complexity_score = comp_result.overall
            except Exception:
                pass  # Scorer is optional

        # Step 4: Assemble context items
        context_items: list[ContextItem] = []

        # CRITICAL: User query
        context_items.append(ContextItem(
            source="user_query",
            content=query,
            priority=ContextPriority.CRITICAL,
            estimated_tokens=len(query) // self.CHARS_PER_TOKEN,
        ))

        # HIGH: Selected tools (schemas)
        if plan_result.selected_tool_names:
            selected_schemas = [
                t for t in available_tools
                if t.get("function", {}).get("name", t.get("name", "")) in plan_result.selected_tool_names
            ]
            schema_text = "\n".join(
                f"- {self._schema_to_text(s)}" for s in selected_schemas
            )
            context_items.append(ContextItem(
                source="tool_schema",
                content=schema_text,
                priority=ContextPriority.HIGH,
                estimated_tokens=len(schema_text) // self.CHARS_PER_TOKEN,
                metadata={"selected_tools": plan_result.selected_tool_names},
            ))

        # HIGH: Wiki hint
        if wiki_hint:
            context_items.append(ContextItem(
                source="wiki",
                content=wiki_hint,
                priority=ContextPriority.HIGH,
                estimated_tokens=len(wiki_hint) // self.CHARS_PER_TOKEN,
            ))

        # HIGH: Skills
        if plan_result.selected_skills:
            for skill in plan_result.selected_skills:
                skill_text = f"### {skill.name}\n{skill.description}\n{skill.content}"
                context_items.append(ContextItem(
                    source="skill",
                    content=skill_text,
                    priority=ContextPriority.HIGH,
                    estimated_tokens=len(skill_text) // self.CHARS_PER_TOKEN,
                    metadata={"name": skill.name, "tags": skill.tags},
                ))

        # MEDIUM: History summary
        if history_summary:
            context_items.append(ContextItem(
                source="history",
                content=history_summary,
                priority=ContextPriority.MEDIUM,
                estimated_tokens=len(history_summary) // self.CHARS_PER_TOKEN,
            ))

        # MEDIUM: MCPs
        if plan_result.selected_mcps:
            mcp_text = "\n".join(
                f"- {m.get('name', 'unknown')}: {m.get('description', '')}"
                for m in plan_result.selected_mcps
            )
            context_items.append(ContextItem(
                source="mcp",
                content=mcp_text,
                priority=ContextPriority.MEDIUM,
                estimated_tokens=len(mcp_text) // self.CHARS_PER_TOKEN,
            ))

        # Step 5: Token budget
        estimated_tokens = sum(item.estimated_tokens for item in context_items)

        # Step 6: Model tier suggestion
        suggested_tier = self._suggest_model_tier(
            intent, complexity_score, estimated_tokens
        )

        return ContextPlan(
            intent=intent,
            intent_confidence=intent_confidence,
            selected_tools=plan_result.selected_tool_names,
            selected_skills=[s.name for s in plan_result.selected_skills],
            selected_mcps=[m.get("name", "") for m in plan_result.selected_mcps],
            context_items=context_items,
            estimated_tokens=estimated_tokens,
            complexity_score=complexity_score,
            suggested_model_tier=suggested_tier,
            reasoning=plan_result.reasoning or f"Intent: {intent.name} (confidence: {intent_confidence:.2f})",
        )

    def _schema_to_text(self, schema: dict[str, Any]) -> str:
        """Convert a tool schema to a compact text description."""
        func = schema.get("function", schema)
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        return f"{name}: {desc}"

    def _suggest_model_tier(
        self,
        intent: IntentType,
        complexity: float,
        estimated_tokens: int,
    ) -> str:
        """Suggest a model tier based on intent, complexity, and token budget.

        Tiers: free < budget < standard < premium < ultra
        """
        # Base tier from intent
        tier_map = {
            IntentType.CONVERSATION: "free",
            IntentType.QUESTION: "budget",
            IntentType.COMMAND: "standard",
            IntentType.CREATIVE: "standard",
            IntentType.ANALYSIS: "premium",
            IntentType.MULTI_STEP: "premium",
        }
        base_tier = tier_map.get(intent, "standard")

        # Upgrade for high complexity
        if complexity > 0.7:
            upgrades = {"free": "budget", "budget": "standard", "standard": "premium", "premium": "ultra", "ultra": "ultra"}
            base_tier = upgrades.get(base_tier, base_tier)

        # Upgrade for long context
        if estimated_tokens > 8000:
            upgrades = {"free": "budget", "budget": "standard", "standard": "premium", "premium": "ultra", "ultra": "ultra"}
            base_tier = upgrades.get(base_tier, base_tier)

        return base_tier
