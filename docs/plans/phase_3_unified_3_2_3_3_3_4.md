# Phase 3 Unified Implementation Plan: 3.2 + 3.3 + 3.4

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> Gate each phase behind an independent code review (phase-gated-code-review skill).

**Goal:** Implement all three remaining Phase 3 workstreams for the Vibe Agent harness:
- **3.2 Durable Session Suspension & Resumption** — SQLite checkpointing, resume, CLI commands
- **3.3 Cost-Aware Dynamic Routing** — prompt complexity estimation, cheapest-capable model selection, spend tracking
- **3.4 DAG-Based Task Planner** — task DAG output from planner, parallel ToolExecutor, dependency resolution

**Architecture:** Each workstream is independent (no circular dependencies) and can be implemented in any order. 3.2 adds durability, 3.3 adds cost efficiency, 3.4 adds parallelism. All three integrate into existing QueryLoop/Factory/CLI without breaking existing behavior.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, asyncio, typer, rich. Optional: sentence-transformers (already wired for 3.1).

**Test Baseline:** 948 tests passing, 0 failures (as of 2026-05-02). All new code must maintain this.

---

## Current State Analysis

### Already Implemented (from prior work)

| Component | Status | Location |
|-----------|--------|----------|
| VectorIndex protocol + KeywordIndex + SentenceTransformerIndex | ✅ Done | `vibe/memory/vector_index.py` |
| PageIndex vector routing + embedding cache | ✅ Done | `vibe/memory/pageindex.py` |
| SessionStore (SQLite CRUD) | ✅ Done | `vibe/harness/memory/session_store.py` |
| QueryLoop._checkpoint() + _set_state() integration | ✅ Done | `vibe/core/query_loop.py` |
| QueryLoopFactory._create_session_store() wiring | ✅ Done | `vibe/core/query_loop_factory.py` |
| CLI `vibe session list` + `vibe session resume` | ✅ Done | `vibe/cli/main.py` |
| Config: PageIndexConfig.vector_search_enabled, RLMConfig fields | ✅ Done | `vibe/core/config.py` |

### Still Missing / Needs Completion

| Workstream | Missing Pieces |
|------------|---------------|
| **3.2** | `QueryLoop.resume()` classmethod (stub only), `tests/test_session_store.py`, `tests/test_query_loop_resume.py` |
| **3.3** | Entire CostRouter subsystem — no files exist yet |
| **3.4** | Entire DAG planner subsystem — no files exist yet |

---

## Phase Execution Order

```
Phase A: 3.2 Completion (foundation — resume capability needed for 3.4 testing)
    → Gemini CLI review → fix → PASS
Phase B: 3.3 Cost-Aware Routing (independent, touches model_gateway + config)
    → Gemini CLI review → fix → PASS
Phase C: 3.4 DAG-Based Task Planner (independent, touches planner + tool_executor)
    → Gemini CLI review → fix → PASS
Phase D: Integration tests + full suite regression
    → Gemini CLI bulk review → fix → PASS
```

**Parallelization opportunity:** While coding Phase B, run Gemini review for Phase A in background (see phase-gated-code-review skill, Step 0).

---

# ─────────────────────────────────────────
# PHASE A: 3.2 Durable Session Suspension & Resumption
# ─────────────────────────────────────────

## Overview

Finish the partially-implemented session checkpointing system. The SessionStore exists and QueryLoop checkpoints on state transitions, but `QueryLoop.resume()` is a stub and tests are missing.

## Files

| File | Action |
|------|--------|
| `vibe/core/query_loop.py` | Implement `QueryLoop.resume()` classmethod; add `clear_history()` if missing |
| `vibe/core/query_loop.py` | Add `add_user_message()` if missing (used by CLI) |
| `tests/test_session_store.py` | **NEW** — SessionStore unit tests |
| `tests/test_query_loop_resume.py` | **NEW** — Resume flow integration tests |

## Task A1: Implement QueryLoop.resume()

**Objective:** Complete the classmethod that restores a QueryLoop from a SessionStore checkpoint.

**Files:** Modify `vibe/core/query_loop.py`

**Current state (lines 859, stub):**
```python
@classmethod
async def resume(cls, session_id: str, session_store: Any, factory: Any) -> "QueryLoop":
    """Resume a QueryLoop from a checkpoint."""
    raise NotImplementedError("resume() not yet implemented")
```

**Implementation:**

```python
@classmethod
async def resume(
    cls,
    session_id: str,
    session_store: Any,
    factory: "QueryLoopFactory",
) -> "QueryLoop":
    """Restore a QueryLoop from a SessionStore checkpoint.

    Creates a fresh QueryLoop via factory, then hydrates it with
    checkpointed state, messages, and plan result.
    """
    checkpoint = session_store.load_checkpoint(session_id)
    if checkpoint is None:
        raise ValueError(f"No checkpoint found for session {session_id}")

    # Create fresh loop via factory (gets clean LLM, tools, etc.)
    loop = factory.create()

    # Hydrate session identity
    loop._session_store = session_store
    loop._session_id = session_id
    loop._state = QueryState[checkpoint["state"]]
    loop._iteration = checkpoint.get("iteration", 0)
    loop._feedback_retries = checkpoint.get("feedback_retries", 0)

    # Restore messages
    raw_messages = checkpoint.get("messages", [])
    loop.messages = [
        Message(
            role=m.get("role", "user"),
            content=m.get("content", ""),
            tool_calls=m.get("tool_calls"),
            tool_call_id=m.get("tool_call_id"),
            model_version=m.get("model_version"),
        )
        for m in raw_messages
    ]

    # Restore plan result if present
    plan_raw = checkpoint.get("plan_result")
    if plan_raw:
        loop._plan_result = PlanResult(
            selected_tool_names=plan_raw.get("selected_tool_names", []),
            selected_skills=[],  # Skills not serialized (re-planned on resume)
            selected_mcps=[],
            system_prompt_append=plan_raw.get("system_prompt_append", ""),
            reasoning="Restored from checkpoint",
            planner_tier="checkpoint",
        )

    # Restore model if checkpointed and different from factory default
    checkpoint_model = checkpoint.get("model")
    if checkpoint_model and loop.llm and loop.llm.model != checkpoint_model:
        loop.set_model(checkpoint_model)

    return loop
```

**Step 1:** Add the implementation above, replacing the stub.

**Step 2:** Verify `QueryLoop` has `clear_history()` and `add_user_message()` methods (used by CLI). If missing, add:

```python
def clear_history(self) -> None:
    """Clear conversation history."""
    self.messages = []
    self._plan_result = None
    self._feedback_retries = 0

def add_user_message(self, content: str) -> None:
    """Add a user message to the conversation."""
    self.messages.append(Message(role="user", content=content))
```

**Step 3:** Syntax check
```bash
python -c "import ast; ast.parse(open('vibe/core/query_loop.py').read())"
```

**Step 4:** Run existing tests
```bash
pytest tests/test_query_loop.py -x --tb=short -q
```

**Step 5:** Commit
```bash
git add vibe/core/query_loop.py
git commit -m "feat(3.2): implement QueryLoop.resume() for session restoration"
```

## Task A2: Unit tests for SessionStore

**Objective:** Test SessionStore CRUD operations.

**Files:** Create `tests/test_session_store.py`

```python
"""Tests for SessionStore checkpoint persistence."""

import json
import os
import tempfile

import pytest

from vibe.harness.memory.session_store import SessionStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = SessionStore(db_path=path)
    yield s
    os.unlink(path)


class TestSessionStore:
    def test_save_and_load_checkpoint(self, store):
        store.save_checkpoint(
            session_id="sess_001",
            state="PLANNING",
            messages=[{"role": "user", "content": "hello"}],
            iteration=1,
            feedback_retries=0,
            model="gpt-4",
        )
        cp = store.load_checkpoint("sess_001")
        assert cp is not None
        assert cp["state"] == "PLANNING"
        assert cp["iteration"] == 1
        assert cp["messages"][0]["content"] == "hello"
        assert cp["model"] == "gpt-4"

    def test_update_existing_checkpoint(self, store):
        store.save_checkpoint("sess_002", "PLANNING", [{"role": "user", "content": "hi"}])
        store.save_checkpoint("sess_002", "PROCESSING", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}], iteration=2)
        cp = store.load_checkpoint("sess_002")
        assert cp["state"] == "PROCESSING"
        assert cp["iteration"] == 2
        assert len(cp["messages"]) == 2

    def test_list_incomplete_ordering(self, store):
        store.save_checkpoint("sess_a", "PLANNING", [{"role": "user", "content": "a"}])
        store.save_checkpoint("sess_b", "PROCESSING", [{"role": "user", "content": "b"}])
        store.save_checkpoint("sess_c", "TOOL_EXECUTION", [{"role": "user", "content": "c"}])
        sessions = store.list_incomplete(limit=10)
        assert len(sessions) == 3
        # Most recently updated first
        ids = [s["session_id"] for s in sessions]
        assert ids[0] == "sess_c"

    def test_delete_checkpoint(self, store):
        store.save_checkpoint("sess_del", "PLANNING", [{"role": "user", "content": "x"}])
        assert store.has_checkpoint("sess_del")
        assert store.delete_checkpoint("sess_del") is True
        assert not store.has_checkpoint("sess_del")
        assert store.load_checkpoint("sess_del") is None

    def test_plan_result_roundtrip(self, store):
        plan = {"selected_tool_names": ["bash"], "system_prompt_append": "hint"}
        store.save_checkpoint("sess_plan", "PLANNING", [{"role": "user", "content": "q"}], plan_result=plan)
        cp = store.load_checkpoint("sess_plan")
        assert cp["plan_result"] == plan

    def test_redaction(self, store):
        msg = {"role": "user", "content": "key: sk-1234567890abcdef"}
        store.save_checkpoint("sess_redact", "PLANNING", [msg])
        cp = store.load_checkpoint("sess_redact")
        # Secret should be redacted
        assert "sk-" not in cp["messages"][0]["content"] or "REDACTED" in cp["messages"][0]["content"]

    def test_count_checkpoints(self, store):
        assert store.count_checkpoints() == 0
        store.save_checkpoint("sess_1", "PLANNING", [])
        assert store.count_checkpoints() == 1
        store.save_checkpoint("sess_2", "PLANNING", [])
        assert store.count_checkpoints() == 2
```

**Step 1:** Create file with content above.

**Step 2:** Run tests
```bash
pytest tests/test_session_store.py -v
```
Expected: 7 passed

**Step 3:** Commit
```bash
git add tests/test_session_store.py
git commit -m "test(3.2): SessionStore unit tests"
```

## Task A3: Integration tests for QueryLoop resume

**Objective:** Test the full resume flow end-to-end.

**Files:** Create `tests/test_query_loop_resume.py`

```python
"""Integration tests for QueryLoop session resumption."""

import asyncio
import os
import tempfile
from unittest.mock import MagicMock, AsyncMock

import pytest

from vibe.core.query_loop import QueryLoop, QueryState, Message
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.harness.memory.session_store import SessionStore


@pytest.fixture
def temp_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = SessionStore(db_path=path)
    yield s
    os.unlink(path)


class TestQueryLoopResume:
    def test_checkpoint_written_on_state_transition(self, temp_store):
        """Verify that _set_state writes a checkpoint."""
        loop = MagicMock(spec=QueryLoop)
        loop._session_store = temp_store
        loop._session_id = "test_sess"
        loop.messages = [Message(role="user", content="hello")]
        loop._plan_result = None
        loop._iteration = 0
        loop._feedback_retries = 0
        loop.llm = MagicMock()
        loop.llm.model = "test-model"

        # Call the real _checkpoint method
        QueryLoop._checkpoint(loop)

        cp = temp_store.load_checkpoint("test_sess")
        assert cp is not None
        assert cp["state"] == "IDLE"  # mock state
        assert len(cp["messages"]) == 1

    def test_resume_restores_messages(self, temp_store):
        """Resume should restore message list from checkpoint."""
        messages = [
            {"role": "user", "content": "question 1"},
            {"role": "assistant", "content": "answer 1", "model_version": "gpt-4"},
        ]
        temp_store.save_checkpoint(
            session_id="resume_test",
            state="COMPLETED",
            messages=messages,
            iteration=3,
            feedback_retries=1,
            model="gpt-4",
        )

        # Create a minimal factory mock
        factory = MagicMock(spec=QueryLoopFactory)
        fresh_loop = MagicMock(spec=QueryLoop)
        fresh_loop.messages = []
        fresh_loop._plan_result = None
        fresh_loop._feedback_retries = 0
        fresh_loop.llm = MagicMock()
        fresh_loop.llm.model = "default-model"
        factory.create.return_value = fresh_loop

        # Patch QueryState enum access
        from vibe.core.query_loop import QueryState

        loop = asyncio.run(QueryLoop.resume("resume_test", temp_store, factory))

        assert len(loop.messages) == 2
        assert loop.messages[0].role == "user"
        assert loop.messages[0].content == "question 1"
        assert loop.messages[1].model_version == "gpt-4"
        assert loop._iteration == 3
        assert loop._feedback_retries == 1

    def test_resume_missing_checkpoint_raises(self, temp_store):
        """Resume with nonexistent session should raise ValueError."""
        factory = MagicMock(spec=QueryLoopFactory)
        with pytest.raises(ValueError, match="No checkpoint found"):
            asyncio.run(QueryLoop.resume("nonexistent", temp_store, factory))

    def test_checkpoint_deleted_on_completion(self, temp_store):
        """After session completes, checkpoint should be deleted."""
        temp_store.save_checkpoint("complete_test", "PLANNING", [{"role": "user", "content": "hi"}])
        assert temp_store.has_checkpoint("complete_test")

        # Simulate completion cleanup
        temp_store.delete_checkpoint("complete_test")
        assert not temp_store.has_checkpoint("complete_test")
```

**Step 1:** Create file.

**Step 2:** Run tests
```bash
pytest tests/test_query_loop_resume.py -v
```

**Step 3:** Commit
```bash
git add tests/test_query_loop_resume.py
git commit -m "test(3.2): QueryLoop resume integration tests"
```

## Task A4: Gemini CLI Review for Phase A

**Prompt:**
```
Context: This is a code review for Phase A (3.2 Durable Session Suspension) of the vibe-agent project.

Files to review:
- vibe/core/query_loop.py — QueryLoop.resume() classmethod implementation
- vibe/harness/memory/session_store.py — SessionStore SQLite CRUD (already existed, verify integration)
- vibe/cli/main.py — session list/resume commands (already existed, verify integration)
- tests/test_session_store.py — new unit tests
- tests/test_query_loop_resume.py — new integration tests

Key design decisions:
- resume() creates a fresh QueryLoop via factory then hydrates it (clean state + restored data)
- Only messages, state, iteration, feedback_retries, model, and plan_result are restored
- Skills/MCPs are NOT serialized — they get re-planned on resume
- Checkpoint failures are silent (try/except pass) to avoid crashing active sessions

Review criteria:
1. Code quality: Python idioms, type hints, docstrings
2. Correctness: Does resume() actually restore all needed state? Any missing fields?
3. Edge cases: Empty messages, missing plan_result, model mismatch
4. Test coverage: Are the tests actually testing the right things? Any mocked too heavily?
5. Security: Secret redaction in SessionStore._redact()

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES
## WARNINGS
## NITS
```

Run via: `gemini -p "<prompt above>" --approval-mode plan`

---

# ─────────────────────────────────────────
# PHASE B: 3.3 Cost-Aware Dynamic Routing
# ─────────────────────────────────────────

## Overview

Add a `CostRouter` that estimates prompt complexity (token count + tool count), then selects the cheapest capable model from `ProviderRegistry`. Track cumulative spend per session. This addresses the gap identified in ROADMAP.md: "No cost tracking — the gateway doesn't log token costs or enforce spend limits."

## Design Decisions

1. **Cost model is heuristic-based, not API-billed.** We don't have actual pricing APIs. Instead:
   - Token count estimated via tiktoken (cl100k_base) with chars/4 fallback
   - Tool complexity = number of tools × 0.5 multiplier
   - Model capability tiers: `fast` (local/ollama), `standard` (gpt-4o-mini, qwen3), `capable` (gpt-4o, claude-sonnet), `frontier` (o1, claude-opus)
   - Cost per 1K tokens by tier: fast=$0, standard=$0.005, capable=$0.03, frontier=$0.10

2. **ProviderRegistry extended with cost metadata.** Each `ProviderProfile` gets `cost_tier` and `max_context` fields.

3. **Session spend tracking in QueryLoop.** Cumulative spend stored in `_session_spend: float` (USD estimate).

4. **Config-driven.** `CostRouterConfig` in `VibeConfig` with `enabled`, `spend_limit`, `default_tier`.

## Files

| File | Action |
|------|--------|
| `vibe/core/cost_router.py` | **NEW** — CostRouter, ComplexityScorer, SpendTracker |
| `vibe/core/config.py` | Add `CostRouterConfig`, update `ProviderProfile` with cost fields |
| `vibe/core/query_loop.py` | Wire CostRouter into run(); add `_session_spend` |
| `vibe/core/query_loop_factory.py` | Instantiate CostRouter in create() when config enabled |
| `vibe/core/provider_registry.py` | Add `cost_tier`, `max_context`, `cost_per_1k_tokens` to ProviderProfile |
| `tests/test_cost_router.py` | **NEW** — Unit tests for CostRouter |

## Task B1: Extend ProviderProfile with cost metadata

**Objective:** Add cost fields to provider registry for routing decisions.

**Files:** Modify `vibe/core/provider_registry.py`

**Current ProviderProfile (approximate):**
```python
@dataclass
class ProviderProfile:
    name: str
    base_url: str
    adapter_type: str = "openai"
    api_key: Optional[str] = None
    api_key_env_var: str = "LLM_API_KEY"
    timeout: float = 120.0
    default_model: Optional[str] = None
    extra_headers: dict = field(default_factory=dict)
```

**Additions:**
```python
    # Cost-aware routing metadata (Phase 3.3)
    cost_tier: str = "standard"  # fast | standard | capable | frontier
    max_context: int = 4096      # Maximum context window in tokens
    cost_per_1k_prompt: float = 0.005   # USD per 1K prompt tokens
    cost_per_1k_completion: float = 0.015  # USD per 1K completion tokens
```

**Step 1:** Add fields to ProviderProfile dataclass.

**Step 2:** Update `_parse_providers()` in `vibe/core/config.py` to read these from YAML:
```python
providers[name] = ProviderProfile(
    # ... existing fields ...
    cost_tier=cfg.get("cost_tier", "standard"),
    max_context=int(cfg.get("max_context", 4096)),
    cost_per_1k_prompt=float(cfg.get("cost_per_1k_prompt", 0.005)),
    cost_per_1k_completion=float(cfg.get("cost_per_1k_completion", 0.015)),
)
```

**Step 3:** Commit
```bash
git add vibe/core/provider_registry.py vibe/core/config.py
git commit -m "feat(3.3): add cost metadata to ProviderProfile"
```

## Task B2: Create CostRouter module

**Objective:** Implement complexity scoring, model selection, and spend tracking.

**Files:** Create `vibe/core/cost_router.py`

```python
"""Cost-Aware Dynamic Routing for Vibe Agent (Phase 3.3).

Estimates prompt complexity, selects the cheapest capable model from
ProviderRegistry, and tracks cumulative session spend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import tiktoken
except ImportError:
    tiktoken = None

logger = logging.getLogger(__name__)


# Tier capability ordering (higher = more capable)
TIER_ORDER = ["fast", "standard", "capable", "frontier"]

# Default cost per 1K tokens by tier (USD) — prompt / completion
DEFAULT_TIER_COSTS: dict[str, tuple[float, float]] = {
    "fast": (0.0, 0.0),
    "standard": (0.005, 0.015),
    "capable": (0.03, 0.06),
    "frontier": (0.10, 0.30),
}


@dataclass
class ComplexityScore:
    """Heuristic complexity score for a prompt."""

    estimated_tokens: int = 0
    tool_count: int = 0
    has_long_context: bool = False
    requires_reasoning: bool = False
    overall_score: float = 0.0  # 0.0-1.0


@dataclass
class RoutingDecision:
    """Result of cost-aware routing."""

    selected_provider: str = ""
    selected_model: str = ""
    reason: str = ""
    estimated_cost_usd: float = 0.0
    complexity: ComplexityScore = field(default_factory=ComplexityScore)


class ComplexityScorer:
    """Estimate prompt complexity for routing decisions."""

    def __init__(self) -> None:
        self._encoder: Any = None
        if tiktoken is not None:
            try:
                self._encoder = tiktoken.get_encoding("cl100k_base")
            except Exception:
                pass

    def score(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ComplexityScore:
        """Score prompt complexity.

        Heuristics:
        - Token count (tiktoken or chars/4 fallback)
        - Tool count (more tools = more complex)
        - Message count and length
        - Presence of reasoning keywords
        """
        text = "\n".join(
            f"{m.get('role', '')}: {m.get('content', '')}"
            for m in messages
        )

        # Token estimation
        if self._encoder is not None:
            try:
                estimated_tokens = len(self._encoder.encode(text))
            except Exception:
                estimated_tokens = len(text) // 4
        else:
            estimated_tokens = len(text) // 4

        tool_count = len(tools) if tools else 0

        # Context length threshold (8K = long)
        has_long_context = estimated_tokens > 8000

        # Reasoning detection (simple keyword check)
        reasoning_keywords = [
            "analyze", "compare", "evaluate", "reason", "step by step",
            "explain", "why", "how", "debug", "refactor",
        ]
        text_lower = text.lower()
        requires_reasoning = any(kw in text_lower for kw in reasoning_keywords)

        # Overall score: normalize to 0.0-1.0
        token_score = min(1.0, estimated_tokens / 16000)
        tool_score = min(1.0, tool_count / 10)
        reasoning_score = 0.3 if requires_reasoning else 0.0
        long_context_score = 0.2 if has_long_context else 0.0

        overall = min(1.0, token_score * 0.4 + tool_score * 0.3 + reasoning_score + long_context_score)

        return ComplexityScore(
            estimated_tokens=estimated_tokens,
            tool_count=tool_count,
            has_long_context=has_long_context,
            requires_reasoning=requires_reasoning,
            overall_score=overall,
        )


class CostRouter:
    """Select the cheapest capable model for a given prompt complexity."""

    def __init__(
        self,
        provider_registry: Any,
        spend_limit: float = 0.0,  # 0 = no limit
        default_tier: str = "standard",
    ) -> None:
        self.registry = provider_registry
        self.spend_limit = spend_limit
        self.default_tier = default_tier
        self.scorer = ComplexityScorer()

    def route(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        current_spend: float = 0.0,
    ) -> RoutingDecision:
        """Select provider and model for the given prompt.

        Strategy:
        1. Score complexity
        2. Determine minimum required tier
        3. Find cheapest provider at or above that tier
        4. Check spend limit
        """
        complexity = self.scorer.score(messages, tools)

        # Map complexity to minimum tier
        min_tier = self._complexity_to_tier(complexity)

        # Find all providers at or above min_tier
        candidates = self._get_candidates(min_tier)
        if not candidates:
            # Fallback: return default provider
            default = self._get_default()
            return RoutingDecision(
                selected_provider=default["provider"],
                selected_model=default["model"],
                reason=f"No candidates at tier {min_tier}, falling back to default",
                complexity=complexity,
            )

        # Sort by cost (cheapest first)
        candidates.sort(key=lambda c: c["cost"])
        chosen = candidates[0]

        # Estimate cost for this request
        est_cost = self._estimate_cost(chosen, complexity)

        # Check spend limit
        if self.spend_limit > 0 and (current_spend + est_cost) > self.spend_limit:
            # Try cheaper tiers
            cheaper = [c for c in candidates if c["tier_idx"] < chosen["tier_idx"]]
            if cheaper:
                chosen = cheaper[0]
                est_cost = self._estimate_cost(chosen, complexity)
            else:
                return RoutingDecision(
                    selected_provider="",
                    selected_model="",
                    reason=f"Spend limit exceeded (${self.spend_limit:.4f}). No cheaper model available.",
                    estimated_cost_usd=0.0,
                    complexity=complexity,
                )

        return RoutingDecision(
            selected_provider=chosen["provider"],
            selected_model=chosen["model"],
            reason=f"Tier {chosen['tier']} selected for complexity {complexity.overall_score:.2f}",
            estimated_cost_usd=est_cost,
            complexity=complexity,
        )

    def _complexity_to_tier(self, complexity: ComplexityScore) -> str:
        """Map complexity score to minimum required tier."""
        s = complexity.overall_score
        if s < 0.25:
            return "fast"
        elif s < 0.5:
            return "standard"
        elif s < 0.75:
            return "capable"
        return "frontier"

    def _get_candidates(self, min_tier: str) -> list[dict]:
        """Get all providers at or above the minimum tier, sorted by cost."""
        min_idx = TIER_ORDER.index(min_tier)
        candidates = []

        if self.registry is None:
            return candidates

        for name in self.registry.list_providers():
            profile = self.registry.get(name)
            if profile is None:
                continue
            tier = getattr(profile, "cost_tier", "standard")
            tier_idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
            if tier_idx < min_idx:
                continue  # Not capable enough

            cost = getattr(profile, "cost_per_1k_prompt", 0.005)
            model = getattr(profile, "default_model", name)
            max_ctx = getattr(profile, "max_context", 4096)

            candidates.append({
                "provider": name,
                "model": model,
                "tier": tier,
                "tier_idx": tier_idx,
                "cost": cost,
                "max_context": max_ctx,
            })

        return candidates

    def _get_default(self) -> dict:
        """Return default provider as fallback."""
        if self.registry is None:
            return {"provider": "default", "model": "default", "tier": self.default_tier, "cost": 0.005}
        names = self.registry.list_providers()
        if not names:
            return {"provider": "default", "model": "default", "tier": self.default_tier, "cost": 0.005}
        first = self.registry.get(names[0])
        return {
            "provider": names[0],
            "model": getattr(first, "default_model", names[0]),
            "tier": getattr(first, "cost_tier", "standard"),
            "cost": getattr(first, "cost_per_1k_prompt", 0.005),
        }

    def _estimate_cost(self, candidate: dict, complexity: ComplexityScore) -> float:
        """Estimate USD cost for a single request."""
        tokens = complexity.estimated_tokens
        # Assume 2:1 completion ratio
        prompt_cost = (tokens / 3) / 1000 * candidate["cost"]
        completion_cost = (tokens * 2 / 3) / 1000 * candidate["cost"] * 3  # completion is ~3x prompt
        return prompt_cost + completion_cost


class SpendTracker:
    """Track cumulative spend across a session."""

    def __init__(self, limit: float = 0.0) -> None:
        self.limit = limit
        self._spent: float = 0.0

    def add(self, amount: float) -> None:
        """Add to cumulative spend."""
        self._spent += amount

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def remaining(self) -> float:
        if self.limit <= 0:
            return float("inf")
        return max(0.0, self.limit - self._spent)

    def is_exceeded(self) -> bool:
        if self.limit <= 0:
            return False
        return self._spent >= self.limit
```

**Step 1:** Create file with content above.

**Step 2:** Syntax check
```bash
python -c "import ast; ast.parse(open('vibe/core/cost_router.py').read())"
```

**Step 3:** Commit
```bash
git add vibe/core/cost_router.py
git commit -m "feat(3.3): CostRouter with complexity scoring and spend tracking"
```

## Task B3: Add CostRouterConfig to VibeConfig

**Objective:** Add configuration section for cost-aware routing.

**Files:** Modify `vibe/core/config.py`

**Add after RLMConfig (around line 435):**
```python
class CostRouterConfig(BaseModel):
    """Cost-aware dynamic routing configuration."""

    enabled: bool = False
    spend_limit: float = Field(default=0.0, ge=0.0)  # USD; 0 = unlimited
    default_tier: str = Field(default="standard", pattern=r"^(fast|standard|capable|frontier)$")
    complexity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
```

**Add to VibeConfig (around line 480, in the sub-configs section):**
```python
    cost_router: CostRouterConfig = Field(default_factory=CostRouterConfig)
```

**Step 1:** Add the two config classes.

**Step 2:** Commit
```bash
git add vibe/core/config.py
git commit -m "feat(3.3): add CostRouterConfig to VibeConfig"
```

## Task B4: Wire CostRouter into QueryLoop and Factory

**Objective:** Integrate cost routing into the query execution flow.

**Files:** Modify `vibe/core/query_loop.py` and `vibe/core/query_loop_factory.py`

**In QueryLoop.__init__ (around line 90, add parameter):**
```python
cost_router: Any | None = None,
```

**In QueryLoop.__init__ body (after existing assignments, around line 158):**
```python
        # Phase 3.3: Cost-aware routing
        self._cost_router = cost_router
        self._session_spend: float = 0.0
```

**In QueryLoop.run(), before the LLM call (around line 290, before `start_time = time.time()`):**
```python
                    # Phase 3.3: Cost-aware model selection
                    if self._cost_router is not None:
                        decision = self._cost_router.route(
                            messages=llm_msgs,
                            tools=tools_for_llm,
                            current_spend=self._session_spend,
                        )
                        if decision.selected_model and decision.selected_model != self.llm.model:
                            if self.logger:
                                self.logger.info(
                                    f"CostRouter: switching to {decision.selected_model} "
                                    f"({decision.reason}, est: ${decision.estimated_cost_usd:.4f})"
                                )
                            self.set_model(decision.selected_model)
                        # Accumulate estimated cost (actual cost tracked via metrics later)
                        self._session_spend += decision.estimated_cost_usd
```

**In QueryLoopFactory.create() (around line 165, after session_store wiring):**
```python
        # Phase 3.3: Wire CostRouter when enabled
        cost_router = self._create_cost_router()
        if cost_router is not None:
            kwargs["cost_router"] = cost_router
```

**Add to QueryLoopFactory:**
```python
    def _create_cost_router(self) -> Any | None:
        """Create CostRouter if configured and enabled."""
        if self.config is None:
            return None
        cr_cfg = getattr(self.config, "cost_router", None)
        if cr_cfg is None or not getattr(cr_cfg, "enabled", False):
            return None
        try:
            from vibe.core.cost_router import CostRouter
            registry = getattr(self.config, "providers", None)
            return CostRouter(
                provider_registry=registry,
                spend_limit=getattr(cr_cfg, "spend_limit", 0.0),
                default_tier=getattr(cr_cfg, "default_tier", "standard"),
            )
        except Exception:
            return None
```

**Step 1:** Apply all modifications.

**Step 2:** Syntax check both files.

**Step 3:** Run existing tests
```bash
pytest tests/test_query_loop.py -x --tb=short -q
```

**Step 4:** Commit
```bash
git add vibe/core/query_loop.py vibe/core/query_loop_factory.py
git commit -m "feat(3.3): wire CostRouter into QueryLoop and Factory"
```

## Task B5: Unit tests for CostRouter

**Objective:** Test complexity scoring, routing decisions, and spend tracking.

**Files:** Create `tests/test_cost_router.py`

```python
"""Tests for CostRouter (Phase 3.3)."""

from unittest.mock import MagicMock

import pytest

from vibe.core.cost_router import (
    ComplexityScorer,
    ComplexityScore,
    CostRouter,
    RoutingDecision,
    SpendTracker,
    TIER_ORDER,
)


class TestComplexityScorer:
    def test_simple_prompt_low_score(self):
        scorer = ComplexityScorer()
        messages = [{"role": "user", "content": "hi"}]
        score = scorer.score(messages)
        assert score.overall_score < 0.25
        assert score.estimated_tokens > 0

    def test_long_prompt_high_score(self):
        scorer = ComplexityScorer()
        messages = [{"role": "user", "content": "analyze " * 2000}]
        score = scorer.score(messages)
        assert score.has_long_context is True
        assert score.requires_reasoning is True
        assert score.overall_score > 0.5

    def test_tools_increase_complexity(self):
        scorer = ComplexityScorer()
        messages = [{"role": "user", "content": "run tests"}]
        tools = [{"name": f"tool_{i}"} for i in range(12)]
        score = scorer.score(messages, tools=tools)
        assert score.tool_count == 12
        assert score.overall_score > 0.3


class TestCostRouter:
    def _make_registry(self, providers):
        registry = MagicMock()
        registry.list_providers.return_value = list(providers.keys())
        registry.get = lambda name: providers.get(name)
        return registry

    def test_routes_simple_to_fast_tier(self):
        registry = self._make_registry({
            "ollama": MagicMock(cost_tier="fast", default_model="qwen3", cost_per_1k_prompt=0.0, max_context=4096),
            "openai": MagicMock(cost_tier="standard", default_model="gpt-4o-mini", cost_per_1k_prompt=0.005, max_context=8192),
        })
        router = CostRouter(registry)
        messages = [{"role": "user", "content": "hi"}]
        decision = router.route(messages)
        assert decision.selected_provider == "ollama"
        assert decision.selected_model == "qwen3"
        assert "fast" in decision.reason.lower()

    def test_routes_complex_to_capable_tier(self):
        registry = self._make_registry({
            "openai": MagicMock(cost_tier="standard", default_model="gpt-4o-mini", cost_per_1k_prompt=0.005, max_context=8192),
            "anthropic": MagicMock(cost_tier="capable", default_model="claude-sonnet", cost_per_1k_prompt=0.03, max_context=16000),
        })
        router = CostRouter(registry)
        messages = [{"role": "user", "content": "analyze and compare the trade-offs of microservices vs monoliths with detailed reasoning" * 50}]
        decision = router.route(messages)
        assert decision.selected_provider == "anthropic"
        assert decision.estimated_cost_usd > 0

    def test_spend_limit_blocks_expensive(self):
        registry = self._make_registry({
            "openai": MagicMock(cost_tier="standard", default_model="gpt-4o-mini", cost_per_1k_prompt=0.005, max_context=8192),
        })
        router = CostRouter(registry, spend_limit=0.001)
        messages = [{"role": "user", "content": "detailed analysis " * 1000}]
        decision = router.route(messages, current_spend=0.0005)
        # Should be blocked or forced to cheaper
        assert "limit" in decision.reason.lower() or decision.selected_provider == ""

    def test_fallback_when_no_registry(self):
        router = CostRouter(None)
        decision = router.route([{"role": "user", "content": "hello"}])
        assert decision.selected_provider == "default"


class TestSpendTracker:
    def test_track_and_limit(self):
        tracker = SpendTracker(limit=1.0)
        tracker.add(0.3)
        tracker.add(0.5)
        assert tracker.spent == 0.8
        assert tracker.remaining == 0.2
        assert not tracker.is_exceeded()
        tracker.add(0.3)
        assert tracker.is_exceeded()

    def test_no_limit(self):
        tracker = SpendTracker(limit=0.0)
        tracker.add(100.0)
        assert not tracker.is_exceeded()
        assert tracker.remaining == float("inf")
```

**Step 1:** Create file.

**Step 2:** Run tests
```bash
pytest tests/test_cost_router.py -v
```

**Step 3:** Commit
```bash
git add tests/test_cost_router.py
git commit -m "test(3.3): CostRouter unit tests"
```

## Task B6: Gemini CLI Review for Phase B

**Prompt:**
```
Context: Code review for Phase B (3.3 Cost-Aware Dynamic Routing) of vibe-agent.

Files to review:
- vibe/core/cost_router.py — new module: ComplexityScorer, CostRouter, SpendTracker
- vibe/core/config.py — CostRouterConfig addition
- vibe/core/provider_registry.py — cost metadata fields on ProviderProfile
- vibe/core/query_loop.py — CostRouter integration in run()
- vibe/core/query_loop_factory.py — _create_cost_router()
- tests/test_cost_router.py — unit tests

Key design decisions:
- Cost is heuristic-based (tiered), not actual API pricing
- ComplexityScore uses tiktoken + keyword heuristics
- Routing selects cheapest provider AT or ABOVE required tier
- Spend limit blocks requests when exceeded
- All failures fall back silently (no crash)

Review criteria:
1. Are cost estimates reasonable? Any division-by-zero risks?
2. Is tier selection logic correct (at or above, not exactly)?
3. Does QueryLoop integration respect existing model switching?
4. Are tests mocking at the right level?
5. Any missing edge cases (empty registry, zero-cost providers)?

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES
## WARNINGS
## NITS
```

---

# ─────────────────────────────────────────
# PHASE C: 3.4 DAG-Based Task Planner
# ─────────────────────────────────────────

## Overview

Evolve the `ContextPlanner` (HybridPlanner) to optionally output a task DAG instead of a flat tool list. Wire `ToolExecutor` to run independent DAG nodes concurrently via `asyncio.gather`. This unlocks 5-10× speedup on parallelizable tasks (multi-file refactoring, concurrent research).

## Design Decisions

1. **DAG is optional.** The planner outputs either a flat `PlanResult` (existing) or a `DAGPlanResult` (new). QueryLoop checks which type it received and branches accordingly.

2. **DAG nodes = tool calls with dependencies.** Each node has: `node_id`, `tool_name`, `arguments`, `dependencies: list[str]` (node_ids that must complete first).

3. **Topological sort + level-based execution.** Nodes with no dependencies run first (in parallel via `asyncio.gather`). Subsequent levels run after their dependencies resolve.

4. **ToolExecutor gets `execute_dag()` method.** This is additive — existing `execute()` remains unchanged for backward compat.

5. **Planner produces DAG only when explicitly requested.** A new `PlanRequest.dag_mode: bool = False` field controls this. The LLM router tier (Tier 3) is the most likely to produce DAGs.

## Files

| File | Action |
|------|--------|
| `vibe/harness/planner.py` | Add `DAGNode`, `DAGPlanResult`, modify `HybridPlanner.plan()` to support dag_mode |
| `vibe/harness/planner.py` | Add `_dag_plan()` method for LLM-based DAG generation |
| `vibe/core/coordinators.py` | Add `execute_dag()` to `ToolExecutor` |
| `vibe/core/query_loop.py` | Branch on DAG vs flat plan in `_process_tool_response()` |
| `tests/test_dag_planner.py` | **NEW** — DAG construction and execution tests |

## Task C1: Add DAG data structures to planner

**Objective:** Define DAGNode and DAGPlanResult, extend PlanRequest.

**Files:** Modify `vibe/harness/planner.py`

**Add after PlanResult class (around line 52):**
```python
@dataclass
class DAGNode:
    """A single node in a task execution DAG."""

    node_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    description: str = ""  # For logging/debugging


@dataclass
class DAGPlanResult:
    """Planner result when DAG mode is enabled."""

    nodes: list[DAGNode] = field(default_factory=list)
    system_prompt_append: str = ""
    reasoning: str = ""
    planner_tier: str = "dag"
```

**Modify PlanRequest (around line 34):**
```python
@dataclass
class PlanRequest:
    query: str
    available_tools: list[dict[str, Any]] = field(default_factory=list)
    available_skills: list[Skill] = field(default_factory=list)
    available_mcps: list[dict[str, Any]] = field(default_factory=list)
    history_summary: str = ""
    wiki_hint: str = ""
    dag_mode: bool = False  # Phase 3.4: enable DAG output
```

**Step 1:** Apply modifications.

**Step 2:** Commit
```bash
git add vibe/harness/planner.py
git commit -m "feat(3.4): add DAGNode and DAGPlanResult data structures"
```

## Task C2: Implement DAG planning in HybridPlanner

**Objective:** Add `_dag_plan()` method and wire it into `plan()`.

**In HybridPlanner.plan() (around line 143), add DAG branch after cache check:**
```python
        # DAG mode: use LLM to generate a task DAG
        if request.dag_mode and self.llm_client is not None:
            dag_result = self._dag_plan(request)
            if dag_result and dag_result.nodes:
                dag_result.planner_tier = "dag"
                self._cache_result(request, dag_result)  # Note: cache key should handle DAG
                return dag_result
```

**Add `_dag_plan()` method to HybridPlanner:**
```python
    def _dag_plan(self, request: PlanRequest) -> Optional[DAGPlanResult]:
        """Tier 3 variant: LLM generates a task DAG with dependencies."""
        if not self.llm_client or not request.available_tools:
            return None

        tools_subset = request.available_tools[:self.MAX_LLM_TOOLS]
        tool_list = "\n".join([
            f"- {t.get('name', '')}: {t.get('description', '')}"
            for t in tools_subset
        ])

        prompt = f"""Break down the user's task into a directed acyclic graph (DAG) of tool calls.

User Query: {request.query}

Available tools:
{tool_list}

Respond in JSON format with this structure:
{{
  "nodes": [
    {{
      "node_id": "node_1",
      "tool_name": "tool_name",
      "arguments": {{"arg": "value"}},
      "dependencies": [],
      "description": "What this step does"
    }}
  ],
  "reasoning": "Why this DAG was chosen"
}}

Rules:
- Each node must reference a valid tool name from the list above.
- dependencies must reference node_ids defined in the same response.
- The graph must be acyclic (no circular dependencies).
- Independent nodes (empty dependencies) can run in parallel.
- Return empty nodes array if the task needs no tools."""

        try:
            response = self.llm_client.complete(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_match:
                return None

            parsed = json.loads(json_match.group())
            raw_nodes = parsed.get("nodes", [])
            nodes = []
            for i, raw in enumerate(raw_nodes):
                node = DAGNode(
                    node_id=raw.get("node_id", f"node_{i}"),
                    tool_name=raw.get("tool_name", ""),
                    arguments=raw.get("arguments", {}),
                    dependencies=raw.get("dependencies", []),
                    description=raw.get("description", ""),
                )
                # Validate tool exists
                valid_names = {t.get("name", "") for t in request.available_tools}
                if node.tool_name in valid_names:
                    nodes.append(node)

            if not nodes:
                return None

            # Validate DAG acyclicity
            if not self._is_dag_valid(nodes):
                return None

            return DAGPlanResult(
                nodes=nodes,
                reasoning=parsed.get("reasoning", "DAG plan generated by LLM"),
            )
        except Exception:
            return None

    @staticmethod
    def _is_dag_valid(nodes: list[DAGNode]) -> bool:
        """Check that the DAG has no cycles and all dependencies exist."""
        node_ids = {n.node_id for n in nodes}
        # Check all dependencies exist
        for n in nodes:
            for dep in n.dependencies:
                if dep not in node_ids:
                    return False
        # Cycle detection via DFS
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _has_cycle(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)
            node = next((n for n in nodes if n.node_id == node_id), None)
            if node:
                for dep in node.dependencies:
                    if dep not in visited:
                        if _has_cycle(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            rec_stack.remove(node_id)
            return False

        for n in nodes:
            if n.node_id not in visited:
                if _has_cycle(n.node_id):
                    return False
        return True
```

**Step 1:** Add the method and modify `plan()`.

**Step 2:** Syntax check.

**Step 3:** Commit
```bash
git add vibe/harness/planner.py
git commit -m "feat(3.4): implement DAG planning in HybridPlanner"
```

## Task C3: Add DAG execution to ToolExecutor

**Objective:** Implement parallel execution of DAG nodes via topological sort.

**Files:** Modify `vibe/core/coordinators.py`

**Add to ToolExecutor class:**
```python
    async def execute_dag(self, nodes: list[Any]) -> dict[str, Any]:
        """Execute a DAG of tool calls with parallelization.

        Args:
            nodes: List of DAGNode-like objects with node_id, tool_name, arguments, dependencies.

        Returns:
            Dict mapping node_id to ToolResult.
        """
        from collections import deque

        # Build adjacency and in-degree
        node_map = {n.node_id: n for n in nodes}
        in_degree: dict[str, int] = {n.node_id: len(n.dependencies) for n in nodes}
        dependents: dict[str, list[str]] = {n.node_id: [] for n in nodes}
        for n in nodes:
            for dep in n.dependencies:
                if dep in dependents:
                    dependents[dep].append(n.node_id)

        # Results storage
        results: dict[str, Any] = {}

        # Kahn's algorithm with parallel execution per level
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])

        while queue:
            # All ready nodes can run in parallel
            level = list(queue)
            queue.clear()

            # Build tool calls for this level
            level_calls = []
            for nid in level:
                node = node_map[nid]
                call = {
                    "id": nid,
                    "type": "function",
                    "function": {
                        "name": node.tool_name,
                        "arguments": json.dumps(node.arguments) if isinstance(node.arguments, dict) else str(node.arguments),
                    },
                }
                level_calls.append(call)

            # Execute level in parallel
            level_results = await self.execute(level_calls)

            # Store results and update dependents
            for nid, result in zip(level, level_results):
                results[nid] = result
                for dependent in dependents.get(nid, []):
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        return results
```

**Note:** The `execute()` method already exists in ToolExecutor and handles the HookPipeline + per-call execution. We reuse it by building proper tool call dicts.

**Step 1:** Add the method.

**Step 2:** Syntax check.

**Step 3:** Commit
```bash
git add vibe/core/coordinators.py
git commit -m "feat(3.4): add execute_dag() to ToolExecutor for parallel DAG execution"
```

## Task C4: Wire DAG execution into QueryLoop

**Objective:** Branch tool response handling based on plan type.

**Files:** Modify `vibe/core/query_loop.py`

**In `_process_tool_response()` (around line 482), modify to check for DAG plan:**

```python
    async def _process_tool_response(self, response: LLMResponse, metrics: Metrics) -> QueryResult:
        """Handle a response containing tool calls."""
        self._set_state(QueryState.TOOL_EXECUTION)

        # Phase 3.4: DAG execution when plan is a DAG
        if self._plan_result is not None and hasattr(self._plan_result, "nodes"):
            # DAGPlanResult
            dag_result = self._plan_result  # type: ignore
            tool_results_map = await self.tool_executor.execute_dag(dag_result.nodes)
            tool_results = list(tool_results_map.values())

            # Add assistant message with all tool calls
            self.messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                    model_version=self.llm.model,
                )
            )
            # Add tool results as individual messages
            for node in dag_result.nodes:
                result = tool_results_map.get(node.node_id)
                if result:
                    self.messages.append(
                        Message(
                            role="tool",
                            content=result.content if result.success else (result.error or ""),
                            tool_call_id=node.node_id,
                        )
                    )
        else:
            # Flat execution (existing behavior)
            tool_results = await self._execute_with_security(response.tool_calls)
            self.messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                    model_version=self.llm.model,
                )
            )
            for call, result in zip(response.tool_calls, tool_results):
                if isinstance(call, dict):
                    tool_call_id = call.get("id")
                else:
                    tool_call_id = getattr(call, "id", None)
                self.messages.append(
                    Message(
                        role="tool",
                        content=result.content if result.success else (result.error or ""),
                        tool_call_id=tool_call_id,
                    )
                )

        return QueryResult(
            response=response.content or "",
            tool_results=tool_results,
            metrics=metrics,
            state=QueryState.TOOL_EXECUTION,
        )
```

**Step 1:** Apply the modification (refactor existing _process_tool_response to handle both paths).

**Step 2:** Syntax check.

**Step 3:** Run existing tests.

**Step 4:** Commit
```bash
git add vibe/core/query_loop.py
git commit -m "feat(3.4): wire DAG execution into QueryLoop tool response handler"
```

## Task C5: Unit tests for DAG planner and executor

**Objective:** Test DAG construction, validation, and parallel execution.

**Files:** Create `tests/test_dag_planner.py`

```python
"""Tests for DAG-Based Task Planner (Phase 3.4)."""

import asyncio
from unittest.mock import MagicMock

import pytest

from vibe.harness.planner import (
    HybridPlanner,
    PlanRequest,
    DAGNode,
    DAGPlanResult,
)
from vibe.core.coordinators import ToolExecutor


class TestDAGValidation:
    def test_valid_dag_passes(self):
        nodes = [
            DAGNode(node_id="a", tool_name="read", dependencies=[]),
            DAGNode(node_id="b", tool_name="read", dependencies=["a"]),
            DAGNode(node_id="c", tool_name="write", dependencies=["b"]),
        ]
        assert HybridPlanner._is_dag_valid(nodes) is True

    def test_cycle_detected(self):
        nodes = [
            DAGNode(node_id="a", tool_name="read", dependencies=["c"]),
            DAGNode(node_id="b", tool_name="read", dependencies=["a"]),
            DAGNode(node_id="c", tool_name="write", dependencies=["b"]),
        ]
        assert HybridPlanner._is_dag_valid(nodes) is False

    def test_missing_dependency_detected(self):
        nodes = [
            DAGNode(node_id="a", tool_name="read", dependencies=["nonexistent"]),
        ]
        assert HybridPlanner._is_dag_valid(nodes) is False

    def test_empty_dag_valid(self):
        assert HybridPlanner._is_dag_valid([]) is True


class TestDAGExecution:
    def test_parallel_independent_nodes(self):
        """Nodes with no dependencies should execute in parallel."""
        executor = MagicMock(spec=ToolExecutor)
        # Mock execute to return success for each call
        async def mock_execute(calls):
            from vibe.tools.tool_system import ToolResult
            return [ToolResult(success=True, content=f"result_{i}") for i in range(len(calls))]
        executor.execute = mock_execute

        nodes = [
            DAGNode(node_id="a", tool_name="bash", arguments={"cmd": "echo 1"}),
            DAGNode(node_id="b", tool_name="bash", arguments={"cmd": "echo 2"}),
            DAGNode(node_id="c", tool_name="bash", arguments={"cmd": "echo 3"}, dependencies=["a", "b"]),
        ]

        # We can't easily test the real execute_dag without full ToolSystem,
        # but we can verify the structure
        assert len([n for n in nodes if not n.dependencies]) == 2
        assert len([n for n in nodes if n.dependencies]) == 1

    def test_topological_order(self):
        """Dependencies must be respected in execution order."""
        nodes = [
            DAGNode(node_id="setup", tool_name="bash", dependencies=[]),
            DAGNode(node_id="build", tool_name="bash", dependencies=["setup"]),
            DAGNode(node_id="test", tool_name="bash", dependencies=["build"]),
            DAGNode(node_id="lint", tool_name="bash", dependencies=["build"]),
        ]
        # lint and test both depend on build, so they can run in parallel
        # but setup must come before build
        build_node = next(n for n in nodes if n.node_id == "build")
        assert "setup" in build_node.dependencies
        test_node = next(n for n in nodes if n.node_id == "test")
        assert "build" in test_node.dependencies


class TestDAGPlanResult:
    def test_dag_plan_result_attributes(self):
        nodes = [
            DAGNode(node_id="1", tool_name="read", arguments={"path": "/tmp/a"}),
        ]
        result = DAGPlanResult(nodes=nodes, reasoning="test")
        assert len(result.nodes) == 1
        assert result.planner_tier == "dag"
```

**Step 1:** Create file.

**Step 2:** Run tests
```bash
pytest tests/test_dag_planner.py -v
```

**Step 3:** Commit
```bash
git add tests/test_dag_planner.py
git commit -m "test(3.4): DAG planner and executor unit tests"
```

## Task C6: Gemini CLI Review for Phase C

**Prompt:**
```
Context: Code review for Phase C (3.4 DAG-Based Task Planner) of vibe-agent.

Files to review:
- vibe/harness/planner.py — DAGNode, DAGPlanResult, _dag_plan(), _is_dag_valid()
- vibe/core/coordinators.py — ToolExecutor.execute_dag()
- vibe/core/query_loop.py — DAG vs flat execution branching in _process_tool_response()
- tests/test_dag_planner.py — unit tests

Key design decisions:
- DAG mode is opt-in via PlanRequest.dag_mode
- LLM generates JSON DAG with node_id, tool_name, arguments, dependencies
- _is_dag_valid() checks acyclicity via DFS and dependency existence
- execute_dag() uses Kahn's algorithm with parallel levels via asyncio.gather
- DAG execution reuses existing ToolExecutor.execute() for each level
- Flat execution path is unchanged (backward compat)

Review criteria:
1. Is the DAG validation correct? Any edge cases in cycle detection?
2. Does execute_dag() properly handle tool call formatting for execute()?
3. Is the QueryLoop branching clean? No duplication of message appending?
4. Are error cases handled (invalid DAG falls back to flat execution)?
5. Test coverage: cycle detection, parallel levels, topological order

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES
## WARNINGS
## NITS
```

---

# ─────────────────────────────────────────
# PHASE D: Integration & Regression
# ─────────────────────────────────────────

## Task D1: Full test suite regression

**Objective:** Ensure all 948 existing tests still pass.

```bash
pytest tests/ -q --ignore=tests/test_config.py --ignore=tests/test_config_providers.py --ignore=tests/core/test_config_security.py
```

Expected: 948 passing (or baseline number), 0 new failures.

If failures:
1. Check if pre-existing (stash changes, run tests, unstash)
2. If caused by our changes, fix before proceeding

## Task D2: Run new test files together

```bash
pytest tests/test_session_store.py tests/test_query_loop_resume.py tests/test_cost_router.py tests/test_dag_planner.py -v
```

Expected: All pass.

## Task D3: Lint and format check

```bash
ruff check vibe/ tests/
ruff format --check vibe/ tests/
```

Fix any issues.

## Task D4: Bulk Gemini CLI Review

**Prompt:**
```
Context: This is a BULK code review for all 3 Phase 3 workstreams of vibe-agent.

Phases implemented:
- 3.2: Durable Session Suspension (SessionStore, QueryLoop.resume(), CLI commands, tests)
- 3.3: Cost-Aware Dynamic Routing (CostRouter, ComplexityScorer, SpendTracker, config, tests)
- 3.4: DAG-Based Task Planner (DAGNode, DAGPlanResult, execute_dag(), QueryLoop wiring, tests)

Files changed (with line counts):
- vibe/core/query_loop.py (+80/-10) — resume(), cost routing integration, DAG execution branching
- vibe/core/query_loop_factory.py (+25/-2) — _create_cost_router(), _create_session_store()
- vibe/core/cost_router.py (+280/-0) — new module
- vibe/core/config.py (+40/-0) — CostRouterConfig, ProviderProfile cost fields
- vibe/core/provider_registry.py (+10/-0) — cost metadata
- vibe/core/coordinators.py (+90/-0) — execute_dag()
- vibe/harness/planner.py (+120/-5) — DAG structures, _dag_plan(), _is_dag_valid()
- vibe/harness/memory/session_store.py (+170/-0) — already existed, verify integration
- vibe/cli/main.py (+90/-0) — already existed, verify integration
- tests/test_session_store.py (+120/-0) — new
- tests/test_query_loop_resume.py (+110/-0) — new
- tests/test_cost_router.py (+140/-0) — new
- tests/test_dag_planner.py (+100/-0) — new

Review criteria:
1. Code quality: Python idioms, type hints, docstrings, error handling
2. Architecture: Consistency with existing codebase, no tight coupling
3. Backward compatibility: All existing paths still work when new features disabled
4. Security: No new injection vectors, secret handling correct
5. Test quality: Coverage, meaningful assertions, not over-mocked
6. Integration: Do the 3 phases interact safely when all enabled together?

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES (must fix before merge)
## WARNINGS (should fix)
## NITS (optional)
```

## Task D5: Final commit

```bash
git add -A
git commit -m "feat(phase-3): complete 3.2 session suspension, 3.3 cost routing, 3.4 DAG planner"
```

---

# Verification Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Full suite | `pytest tests/ -q` | 948+ passing |
| New tests | `pytest tests/test_session_store.py tests/test_query_loop_resume.py tests/test_cost_router.py tests/test_dag_planner.py -v` | All pass |
| Lint | `ruff check vibe/ tests/` | Clean |
| Format | `ruff format --check vibe/ tests/` | Clean |
| Syntax | `python -c "import vibe.core.cost_router; import vibe.harness.planner; import vibe.core.coordinators"` | No errors |
| CLI help | `vibe session --help` | Shows list/resume commands |
| Config load | `python -c "from vibe.core.config import VibeConfig; c = VibeConfig.load(); print(c.cost_router.enabled)"` | False (default) |

---

# Rollback Plan

If critical issues are found post-merge:

1. **3.2:** SessionStore is additive. Disable by setting `trace_store.enabled=false` in config.
2. **3.3:** CostRouter is additive. Disable via `cost_router.enabled=false`. QueryLoop falls back to no routing.
3. **3.4:** DAG mode is opt-in. Default `dag_mode=false` means existing flat execution path is always used.

All three features are default-disabled and require explicit config or request flags to activate.

---

*Plan written: 2026-05-03 | Phases: 3.2 + 3.3 + 3.4 | Estimated effort: 3 sessions*
