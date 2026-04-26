# Vibe-Agent Harness Improvement Plan v1.2

> **Date:** 2026-04-25  
> **Scope:** Improvements to the existing vibe-agent harness based on actual codebase audit and architectural critique.  
> **Inputs:** Live source code audit (`vibe/`, `tests/`, `docs/`) + Architectural critique findings.

---

## 1. Current State Assessment (From Live Code Audit)

### What's Already Built (Solid Foundation)

| Component | Status | Location |
|-----------|--------|----------|
| **Query Loop** | ✅ State machine (IDLE→PLANNING→PROCESSING→TOOL_EXECUTION→SYNTHESIZING→COMPLETED) | `vibe/core/query_loop.py` |
| **Context Compaction** | ✅ Exists with TRUNCATE / LLM_SUMMARIZE / OFFLOAD / DROP strategies + tiktoken | `vibe/core/context_compactor.py` |
| **Coordinators** | ✅ ToolExecutor, FeedbackCoordinator, CompactionCoordinator extracted | `vibe/core/coordinators.py` |
| **Model Gateway** | ✅ Circuit breaker, fallback chain, multi-provider registry, structured output | `vibe/core/model_gateway.py` |
| **Error Recovery** | ✅ Exponential backoff with jitter, retryable exception classification | `vibe/core/error_recovery.py` |
| **Eval Infrastructure** | ✅ 30+ YAML cases, runner, multi-model scorecards, soak tests | `vibe/evals/` |
| **Observability** | ✅ Spans, counters, gauges, histograms, export to JSON | `vibe/evals/observability.py` |
| **Skill System v2** | ✅ TOML frontmatter, Pydantic models, parser, validator, installer, executor | `vibe/harness/skills/` |
| **Sync Delegate** | ✅ asyncio.Semaphore, factory-per-task, timeout | `vibe/harness/orchestration/sync_delegate.py` |
| **Hook Pipeline** | ✅ 5 stages (PRE_VALIDATE → PRE_MODIFY → PRE_ALLOW → POST_EXECUTE → POST_FIX) | `vibe/harness/constraints.py` |
| **Trace Store** | ✅ SQLite sessions/messages/tool_calls/embeddings, vector fallback to keyword | `vibe/harness/memory/trace_store.py` |
| **Eval Store** | ✅ SQLite evals + results, schema migrations | `vibe/harness/memory/eval_store.py` |

### What's Weak / Needs Improvement

| Component | Problem | Severity |
|-----------|---------|----------|
| **ContextPlanner** | Keyword substring scoring; "return all tools" fallback defeats token savings. Missing fast-path logic. | P0 |
| **TraceStore** | Loads ALL embeddings into memory; uses `pickle`; no index. O(N) memory scaling. | P0 |
| **EvalRunner** | Reuses QueryLoop instance; `clear_history()` doesn't isolate subordinate state; closes loop in `finally` | P0 |
| **FeedbackEngine** | Bare `except Exception:` → silent `score=0.5`; destroys measurement signal | P0 |
| **SkillExecutor** | Naive `.replace()` partial-match bugs; conflicts with shell variables. | P0 |
| **ContextCompactor** | Placeholder summaries; LLM_SUMMARIZE strategy rarely invoked; lacks anti-thrashing metrics. | P1 |
| **Security Hooks** | Only 2 hooks with ~4 patterns; no file safety, URL safety, secret redaction. Uninstrumented. | P1 |
| **Wiki Memory** | Simple read/write files; no compilation from traces; risks unverified autonomous overwrites. | P1 |
| **Config Schema** | Missing strict Pydantic validation for new complex configurations (Budgets, Security). | P1 |
| **Budget Governance** | No IterationBudget; no tool-result size caps; no grace call | P2 |
| **Checkpointing** | No filesystem rollback; no shadow git | P2 |
| **Async Orchestration** | Only sync delegate; no spawn/steer/kill/flow primitives | P2 |

---

## 2. Design Principles (Synthesized from References)

| # | Principle | Rationale |
|---|-----------|-----------|
| 1 | **The harness is the product** | Model quality is commoditized; harness quality is the moat. |
| 2 | **Eval-driven or it didn't happen** | Every harness change must move the `vibe eval` suite. |
| 3 | **Fail closed, degrade gracefully** | Security defaults to deny; features fall back to safe behavior. |
| 4 | **Immutable state boundaries** | Never mutate shared lists in-place; formalize state through events. |
| 5 | **Turn-level atomic operations** | Evict / compress / delegate entire turns — never split tool_call/tool_result pairs. |
| 6 | **Tiered intelligence** | Pay for smarts only when needed: fast path → embedding fallback → LLM escape hatch. |
| 7 | **Observable everything** | Distinguish "failed to measure" from "neutral measurement." Never silently swallow. |
| 8 | **Isolation by default** | Every eval case, every subagent, every skill execution gets a clean context boundary. |

---

## 3. Improvement Areas

### 3.1 ContextPlanner → Hybrid Semantic Planner (P0)

**Target:** Three-tier planner with caching and a High-Confidence Fast-Path to minimize latency.

```
User Query
    │
    ▼
┌─────────────────┐     max score > threshold?
│ Keyword Fast-Path│ ──Yes──► Return matched tools
│ (current, free)  │
└─────────────────┘
    │ No
    ▼
┌─────────────────┐     similarity > 0.8? (High-Confidence Fast-Path)
│ Embedding Scorer │ ──Yes──► Return top-K tools
│ (MiniLM, local)  │
└─────────────────┘
    │ No (0.3 < similarity <= 0.8)
    ▼
┌─────────────────┐
│ LLM Router Call  │ ──Always──► Return selected tools
│ (cheap model,    │            Cache result for 1hr
│  json output)    │
└─────────────────┘
```

**Implementation:**
- **High-Confidence Fast-Path:** Skip the LLM Router if embedding similarity is > 0.8 to keep CLI snappy.
- **Tool Embedding Cache:** SQLite table `tool_embeddings` (384-dim blobs). Refresh when tool set changes.
- **Query Cache:** LRU cache keyed by `(query_hash, tool_set_hash)`. TTL = 1 hour.
- **Observability:** Add `planner_tier_eval` spans with attributes `tier_used`, `similarity_score`.

---

### 3.2 TraceStore → Scalable Vector Memory (P0)

**Target:** SQLite-native approximate pre-filtering + raw float storage to fix O(N) memory scaling.

**Implementation:**
1. **Replace `pickle` with raw float32 blobs:** `emb.astype(np.float32).tobytes()`
2. **Random-projection signatures for fast pre-filtering:** Fixed-seed 64×384 projection matrix. Store 64-bit integer signature. Pre-filter using Hamming distance bitmask.
3. **Time-window default:** Limit to last 90 days. Add index on `sessions(start_time)`.

---

### 3.3 EvalRunner → Factory-Per-Case Isolation (P0)

**Target:** Fresh `QueryLoop` per case via factory to prevent history and state bleed across concurrent evals.

**Implementation:**
- Change `EvalRunner` constructor to accept `QueryLoopFactory`.
- `run_case()` calls `factory.create()` → fresh loop → run → close.
- Add `diff_score` column schema migration to `EvalStore`.

---

### 3.4 FeedbackEngine → Structured Failure Taxonomy (P0)

**Target:** Distinguish measurement failure from actual low score.

**Implementation:**
- Introduce `FeedbackStatus` enum (`SUCCESS`, `PARSE_ERROR`, `API_ERROR`, `TIMEOUT`, `VALIDATION_ERROR`).
- Return structured `FeedbackResult`.
- Emit `feedback_attempts_total{status=...}` metric.

---

### 3.5 SkillExecutor → Environment Variable Passing (P0)

**Target:** Eliminate template injection bugs and bash variable syntax conflicts (`${VAR}`).

**Implementation:**
- **Execution via Environment Variables:** Instead of string replacement, pass variables in the `env` dictionary to the `BashTool`.
- The bash command directly references `$KEY` or `${KEY}`, relying on the secure boundary of the subprocess environment rather than string mangling.

---

### 3.6 ContextCompactor → Real LLM Summarization with Efficiency Metrics (P1)

**Target:** Structured summarization with anti-thrashing observability.

**Implementation:**
- Implement Hermes-style structured prompt.
- **Compaction Efficiency Metric:** Add `compaction_efficiency` (tokens saved / tokens spent on summary) to `Observability`.
- **Anti-thrashing:** If efficiency drops below 1.5x over 2 turns, fallback to `TRUNCATE` or `DROP`.

---

### 3.7 Security Hardening — Defense in Depth & Strict Configuration (P1)

**Target:** 5-layer security model + Strict Pydantic Config.

**Implementation:**
- **Layer 1-5 Security:** Regex denylists, `FileSafetyGate`, `URLSafetyGate`, execution sandboxing, secret redaction.
- **Pydantic Validation:** Create strict Pydantic v2 models for `SecurityConfig`, `BudgetConfig`, `MemoryConfig` to prevent malformed configurations from causing silent failures.
- **Observability:** Add `security_hook_eval` spans detailing `hook_name`, `blocked_reason`, and `action`.

---

### 3.8 Wiki Memory Compiler — Human-in-the-Loop Review (P2)

**Target:** Nightly trace compilation with a `pending/` review mechanism to prevent unverified overwrites.

**Implementation:**
- Compiler scans trace store for successful sessions.
- Generates candidate wiki updates and writes them to a `~/.vibe/wiki/pending/` directory.
- Requires human review and manual promotion to `compiled/`. Agent *never* autonomously overwrites `compiled/` files.

---

### 3.9 Budget Governance & Checkpointing (P2)
*(Remains unchanged from v1.1: IterationBudget, Result size caps, shadow git checkpoints).*

### 3.10 Async Orchestration Primitives (P2)
*(Remains unchanged from v1.1: SessionStore, AsyncDelegate, TaskFlow).*

---

## 4. Implementation Phases

### Phase 1: Critical Fixes & Config (Weeks 1-2)
**Goal:** Fix the P0 issues and formalize configuration. All existing evals must pass.
- Hybrid Semantic Planner (with fast-path)
- Scalable TraceStore (signatures + raw blobs)
- Factory-per-case EvalRunner
- Structured FeedbackEngine
- SkillExecutor Env Var Passing
- Pydantic Config Schema Migration

### Phase 2: Reliability, Depth & Observability (Weeks 3-5)
**Goal:** Real summarization, security expansion, wiki memory.
- Real LLM Summarization in Compactor (with efficiency metrics)
- Security hardening (Layer 1-2) + `security_hook_eval` spans
- Secret Redaction module
- Wiki Memory Compiler (Pending Directory)
- IterationBudget + BudgetConfig

### Phase 3 & 4: Advanced Features & Orchestration (Weeks 6-11)
*(Rollback, Smart Approval, Async Delegates, TaskFlows, `vibe optimize`)*

---
*Plan version: 1.2*  
*Next step: Break Phase 1 into GitHub issues and assign priorities.*
