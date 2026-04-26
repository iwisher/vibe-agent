# Vibe-Agent Harness Improvement Plan v1.2.1

> **Date:** 2026-04-25  
> **Scope:** Improvements to the existing vibe-agent harness based on actual codebase audit and architectural critique.  
> **Inputs:** Live source code audit (`vibe/`, `tests/`, `docs/`) + Architectural critique findings.  
> **Delta from v1.2:** Amendments to SkillExecutor approach (env-var + template fallback), planner safety fallback, and restored actionable sections (metrics, file tree, effort estimates).

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
│ LLM Router Call  │ ──Success──► Return selected tools
│ (cheap model,    │            Cache result for 1hr
│  json output)    │
└─────────────────┘
    │ Failure / timeout / no match
    ▼
┌─────────────────┐
│ Safety Fallback  │ ──Always──► Return ALL tools
│ (never starve)   │            Log metric: planner_fallback_total
└─────────────────┘
```

**Implementation:**
- **High-Confidence Fast-Path:** Skip the LLM Router if embedding similarity is > 0.8 to keep CLI snappy.
- **Tool Embedding Cache:** SQLite table `tool_embeddings` (384-dim np.float32 blobs). Refresh when tool set changes.
- **Query Cache:** LRU cache keyed by `(query_hash, tool_set_hash)`. TTL = 1 hour.
- **LLM Router:** Minimal prompt → JSON `{"selected_tools": ["name1", "name2"]}`. Use cheapest configured model.
- **Safety Fallback:** If LLM router fails (timeout, parse error, API error), return all tools and emit `planner_fallback_total` metric. The agent is never starved.
- **Observability:** Add `planner_tier_eval` spans with attributes `tier_used`, `similarity_score`.

**New eval cases:** Add `planner_semantic_*.yaml` tests (e.g., query "fetch weather" must select `fetch_forecast` even if description lacks "weather").

---

### 3.2 TraceStore → Scalable Vector Memory (P0)

**Target:** SQLite-native approximate pre-filtering + raw float storage to fix O(N) memory scaling.

**Implementation:**
1. **Replace `pickle` with raw float32 blobs:**
   ```python
   # Store
   blob = emb.astype(np.float32).tobytes()
   # Retrieve
   emb = np.frombuffer(row["embedding"], dtype=np.float32)
   ```

2. **Random-projection signatures for fast pre-filtering:**
   - Fixed-seed 64×384 projection matrix (checked into repo).
   - `signature = np.sign(embedding @ R.T)` → 64-bit integer.
   - Store as INTEGER in SQLite.
   - Pre-filter using Hamming distance bitmask.
   - Only load + score ~5-10% candidates in Python.

3. **Time-window default:** Limit to last 90 days. Add index on `sessions(start_time)`.

4. **Schema migration:**
   ```sql
   ALTER TABLE session_embeddings ADD COLUMN signature INTEGER;
   ALTER TABLE session_embeddings ADD COLUMN updated_at TEXT;
   CREATE INDEX idx_sessions_start_time ON sessions(start_time);
   CREATE INDEX idx_embeddings_signature ON session_embeddings(signature);
   ```

---

### 3.3 EvalRunner → Factory-Per-Case Isolation (P0)

**Target:** Fresh `QueryLoop` per case via factory to prevent history and state bleed across concurrent evals.

**Implementation:**
- Change `EvalRunner` constructor to accept `QueryLoopFactory` instead of `QueryLoop` instance.
- `run_case()` calls `factory.create()` → fresh loop → run → close.
- `MultiModelRunner` and `SoakTestRunner` already do this correctly. `EvalRunner` should match.

```python
# Before:
class EvalRunner:
    def __init__(self, query_loop: QueryLoop, ...): ...

# After:
class EvalRunner:
    def __init__(self, query_loop_factory: QueryLoopFactory, ...): ...
```

**Also fix:** `EvalResult` dataclass has `diff_score` field but `eval_results` table has no column. Add migration.

---

### 3.4 FeedbackEngine → Structured Failure Taxonomy (P0)

**Target:** Distinguish measurement failure from actual low score.

**Implementation:**
- Introduce `FeedbackStatus` enum:
  ```python
  class FeedbackStatus(Enum):
      SUCCESS = "success"
      PARSE_ERROR = "parse_error"
      API_ERROR = "api_error"
      TIMEOUT = "timeout"
      VALIDATION_ERROR = "validation_error"
  ```
- Return structured `FeedbackResult(status, score, issues, suggested_fix, error_type)`.
- **Caller behavior in `FeedbackCoordinator.evaluate()`:**
  - `SUCCESS` + score < threshold → inject retry hint (current behavior)
  - `SUCCESS` + score >= threshold → complete
  - `TIMEOUT` / `API_ERROR` → retry up to 2× with backoff, then complete with warning
  - `PARSE_ERROR` / `VALIDATION_ERROR` → log as harness bug, complete without retry
- Emit `feedback_attempts_total{status=...}` metric.

---

### 3.5 SkillExecutor → Environment Variable Passing with Template Fallback (P0)

**Target:** Eliminate template injection bugs and bash variable syntax conflicts (`${VAR}`).

**Primary Approach — Environment Variables:**
- Pass variables in the `env` dictionary to `BashTool.execute()`.
- Bash command references `$KEY` or `${KEY}` natively.
- Rely on the subprocess environment boundary rather than string mangling.

**Fallback Approach — `string.Template`:**
- When a variable name is not a valid env identifier (contains `-`, `.`, etc.), or when the skill step declares `pass_via: template`.
- Use `string.Template(command).substitute(mapping)` for safe substitution.
- Raises `KeyError` on missing variables instead of silent empty-string expansion.

**Migration:**
- Accept both `{key}` (legacy), `${key}` (template new), and `$key` (env var new) during deprecation window.
- `SkillValidator` checks: (a) ambiguous variable names, (b) variables that aren't valid env identifiers when using env mode.

**Trade-offs:**

| Approach | Pros | Cons |
|----------|------|------|
| Env vars | Zero string mangling; bash handles quoting; no injection | Platform limits; invalid env names fail silently; coupled to BashTool |
| string.Template | Stdlib; works with any tool; clear errors | Still string-based; must handle quoting |

**Decision:** Use env vars as primary for bash skills, `string.Template` as fallback for edge cases. This gives maximum security for the common path without painting ourselves into a corner.

---

### 3.6 ContextCompactor → Real LLM Summarization with Efficiency Metrics (P1)

**Target:** Structured summarization with anti-thrashing observability.

**Implementation:**
- Implement Hermes-style structured prompt:
  ```
  Summarize the following conversation turns:
  - Active Task
  - Goal
  - Completed Actions
  - Key Decisions
  - Relevant Files
  - Remaining Work
  ```
- Wire `summarize_fn` to the QueryLoop's `llm` client.
- **Compaction Efficiency Metric:** Add `compaction_efficiency` (tokens saved / tokens spent on summary) to `Observability`.
- **Anti-thrashing:** If efficiency drops below 1.5× over 2 consecutive compactions, fallback to `TRUNCATE` or `DROP`.

---

### 3.7 Security Hardening — Defense in Depth & Strict Configuration (P1)

**Target:** 5-layer security model + strict Pydantic config validation.

```
Layer 1: Static Analysis (before execution)
├── Regex denylist (~30 patterns: rm -rf, curl | bash, sudo, fork bomb, etc.)
├── Invisible unicode detection (zero-width chars)
└── Skill command scanning (expand existing SkillValidator)

Layer 2: Policy Engine (pre-hooks)
├── PermissionGate: destructive ops require user_approved metadata
├── PolicyGate: expanded regex blocking with category tags
├── FileSafetyGate: write denylist (~/.ssh, /etc, etc.)
├── PathTraversalGate: resolve() + relative_to() validation
└── URLSafetyGate: SSRF prevention (metadata IPs, link-local)

Layer 3: Smart Approval (optional LLM-as-judge)
├── When pattern matches + config.approvals.mode == "smart"
├── Cheap model evaluates actual risk
└── APPROVE / DENY / ESCALATE

Layer 4: Execution Sandboxing
├── BashTool already uses subprocess_exec (no shell)
├── Add Docker sandbox option for untrusted code
└── Process group isolation + resource limits

Layer 5: Audit & Redaction
├── Secret redaction (40+ patterns)
├── Structured audit log to TraceStore
└── Session persistence of all security events
```

**Pydantic Config:**
- Create strict Pydantic v2 models: `SecurityConfig`, `BudgetConfig`, `MemoryConfig`.
- Prevent malformed configurations from causing silent failures.

**Observability:**
- Add `security_hook_eval` spans detailing `hook_name`, `blocked_reason`, and `action`.

**New eval cases:** Add `security_hook_*.yaml`, `file_traversal_security_*.yaml`.

---

### 3.8 Wiki Memory Compiler — Human-in-the-Loop Review (P2)

**Target:** Nightly trace compilation with a `pending/` review mechanism to prevent unverified overwrites.

**Implementation:**
- Compiler scans trace store for successful sessions.
- Generates candidate wiki updates and writes them to `~/.vibe/wiki/pending/`.
- Requires human review and manual promotion to `compiled/`.
- Agent **never** autonomously overwrites `compiled/` files.

**Directory structure:**
```
~/.vibe/wiki/
├── compiled/           # Human-editable "Compiled Truth"
│   ├── projects/
│   ├── concepts/
│   ├── conventions/
│   └── preferences/
├── pending/            # Agent-generated candidates awaiting review
├── timeline/           # Append-only evidence from traces
│   └── YYYY-MM-DD-session-xxx.md
└── index.json          # Metadata + last compilation time
```

**Integration:** ContextPlanner checks `compiled/` page titles before planning; injects matches into `system_prompt_append`.

---

### 3.9 Budget Governance & Checkpointing (P2)

**IterationBudget:**
```python
@dataclass
class IterationBudget:
    max_turns: int = 50
    remaining: int = 50
    
    def consume(self) -> bool:
        if self.remaining > 0:
            self.remaining -= 1
            return True
        return False
    
    def refund(self, n: int = 1) -> None:
        self.remaining = min(self.remaining + n, self.max_turns)
```

**BudgetConfig (tool result size caps):**
```python
@dataclass
class BudgetConfig:
    default_result_size_chars: int = 100_000
    turn_budget_chars: int = 200_000
    preview_size_chars: int = 1_500
    per_tool_overrides: dict[str, int] = field(default_factory=dict)
```

**CheckpointManager:**
- Shadow git repos under `~/.vibe/checkpoints/{project_hash}/`
- Auto-snapshot before `write_file`, destructive `bash`
- CLI: `/rollback <N>` and `/rollback <N> <file>`
- Prune to max 50 snapshots
- Respect `.gitignore`

---

### 3.10 Async Orchestration Primitives (P2)

**Phase 2a — Session Store:**
```python
class SessionStore:
    def create_session(self, parent_id: str | None, ...) -> Session
    def get_session(self, session_id: str) -> Session
    def list_children(self, parent_id: str) -> list[Session]
    def update_status(self, session_id: str, status: SessionStatus)
```

**Phase 2b — Async Subagent Spawner:**
```python
class AsyncDelegate:
    async def spawn(self, task: SpawnTask) -> SpawnResult  # non-blocking
    async def steer(self, session_id: str, message: str) -> None  # mid-flight
    async def kill(self, session_id: str) -> None  # cascading interrupt
```

**Phase 2c — TaskFlow (Managed Iterative Workflows):**
```python
class TaskFlow:
    status: TaskFlowStatus  # queued | running | waiting | blocked | succeeded | failed | cancelled | lost
    async def run_task(self, ...) -> TaskRecord
    async def set_waiting(self, ...) -> None
    async def resume(self, new_state: dict) -> None
    async def finish(self, result: str) -> None
    async def fail(self, reason: str) -> None
```

**Phase 2d — Topology Planner:**
```python
class TopologyPlanner:
    def plan(self, task: str, subtasks: list[str]) -> TopologyPlan:
        # Independent + short → SyncDelegate
        # Steering needed + >5 min → Async sessions
        # External agent → Async ACP bridge
```

---

## 4. Implementation Phases

### Phase 1: Critical Fixes & Config (Weeks 1-2)
**Goal:** Fix the 5 P0 issues and formalize configuration. All existing evals must pass.

| Task | Section | Effort |
|------|---------|--------|
| Hybrid Semantic Planner | 3.1 | 3 days |
| Scalable TraceStore (signatures + raw blobs) | 3.2 | 2 days |
| Factory-per-case EvalRunner | 3.3 | 1 day |
| Structured FeedbackEngine | 3.4 | 2 days |
| SkillExecutor Env Var + Template Fallback | 3.5 | 1.5 days |
| Pydantic Config Schema Migration | 3.7 | 1 day |
| Add diff_score column migration | 3.3 | 0.5 day |
| **Total** | | **~11 days** |

**Deliverable:** All 30+ existing evals pass + 5 new evals for planner + security.

---

### Phase 2: Reliability, Depth & Observability (Weeks 3-5)
**Goal:** Real summarization, security expansion, wiki memory.

| Task | Section | Effort |
|------|---------|--------|
| Real LLM Summarization in Compactor (with efficiency metrics) | 3.6 | 3 days |
| Security hardening (Layer 1-2) + `security_hook_eval` spans | 3.7 | 3 days |
| Secret Redaction module | 3.7 | 2 days |
| Audit logging to TraceStore | 3.7 | 1 day |
| Wiki Memory Compiler (Pending Directory) | 3.8 | 2 days |
| IterationBudget + BudgetConfig | 3.9 | 2 days |
| **Total** | | **~13 days** |

**Deliverable:** Agent handles 50+ turn sessions; security evals pass; wiki compiles from traces with human review.

---

### Phase 3: Advanced Features (Weeks 6-8)
**Goal:** Checkpointing, smart approval, async foundations.

| Task | Section | Effort |
|------|---------|--------|
| CheckpointManager (shadow git) | 3.9 | 3 days |
| Smart Approval Gate (LLM-as-judge) | 3.7 | 2 days |
| Session Store (SQLite) | 3.10 | 2 days |
| AsyncDelegate (spawn/steer/kill) | 3.10 | 4 days |
| **Total** | | **~11 days** |

**Deliverable:** Filesystem rollback works; async subagent primitives exist.

---

### Phase 4: Orchestration & Hill-Climbing (Weeks 9-11)
**Goal:** TaskFlows, topology planner, auto-optimization.

| Task | Section | Effort |
|------|---------|--------|
| TaskFlow engine | 3.10 | 4 days |
| Topology Planner | 3.10 | 2 days |
| Trace mining for eval generation | — | 3 days |
| Harness Optimizer (`vibe optimize`) | — | 4 days |
| **Total** | | **~13 days** |

**Deliverable:** `vibe optimize` runs suite, diagnoses failures, proposes harness edits, validates holdout set.

---

## 5. File-Level Changes

```
vibe/
├── harness/
│   ├── constraints.py              # Expand: file_safety, url_safety hooks
│   ├── feedback.py                 # Add FeedbackStatus enum, structured errors
│   ├── planner.py                  # Hybrid planner with embedding + LLM fallback
│   ├── instructions.py             # Unchanged
│   ├── memory/
│   │   ├── trace_store.py          # Signatures, raw blobs, time-window queries
│   │   ├── eval_store.py           # Add diff_score column
│   │   ├── wiki.py                 # Add compiler job, compiled/timeline/pending dirs
│   │   └── session_store.py        # NEW: async orchestration session registry
│   ├── orchestration/
│   │   ├── sync_delegate.py        # Unchanged
│   │   ├── async_delegate.py       # NEW: spawn/steer/kill
│   │   ├── task_flow.py            # NEW: managed iterative workflows
│   │   └── topology_planner.py     # NEW: sync vs async decision engine
│   ├── skills/
│   │   ├── executor.py             # Env-var passing + string.Template fallback
│   │   └── validator.py            # Add invisible unicode, expanded patterns
│   └── security/                   # NEW MODULE
│       ├── denylist.py             # Regex patterns (port from Hermes)
│       ├── file_safety.py          # Write denylist, path traversal
│       ├── url_safety.py           # SSRF prevention
│       ├── smart_approval.py       # LLM-as-judge gate
│       ├── secret_redaction.py     # 40+ redaction patterns
│       └── audit.py                # Audit logging to trace store
├── core/
│   ├── context_compactor.py        # Wire summarize_fn to LLM; efficiency metrics
│   ├── checkpoint_manager.py       # NEW: shadow git snapshots
│   └── budget.py                   # NEW: IterationBudget + BudgetConfig
└── evals/
    ├── builtin/
    │   ├── planner_semantic_*.yaml # NEW
    │   ├── security_*.yaml         # NEW
    │   └── long_running_*.yaml     # NEW
    └── runner.py                   # Factory-per-case, observability integration
```

---

## 6. Success Metrics

| Metric | Baseline (Current) | Phase 1 Target | Phase 2 Target | Phase 4 Target |
|--------|-------------------|----------------|----------------|----------------|
| Eval pass rate | ~70% (estimated) | 85% | 92% | 95% |
| Planner token waste | 100% (all tools fallback) | <30% | <20% | <15% |
| TraceStore query latency @ 10k | ~500ms | <100ms | <50ms | <20ms |
| Max session turns before failure | ~15 | 30 | 50 | 100+ |
| Security hook coverage | 4 patterns | 30 patterns | 50 patterns | 70+ patterns |
| Eval isolation failures | Unknown | 0 | 0 | 0 |
| Feedback failure observability | 0% | 100% | 100% | 100% |
| Context compaction quality | Placeholder summaries | Real LLM summaries | Real summaries + anti-thrash | Turn-level atomic eviction |
| Subagent concurrency | 3 sync | 3 sync | 3 sync | 3 sync + 5 async |

---

## 7. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Context compactor quality is poor** | Medium | High | Make strategy pluggable; safe default is DROP whole turns. |
| **Semantic planner LLM latency** | Medium | Medium | Cache aggressively; high-confidence fast-path (0.8) catches most cases; embedding fallback catches 80%. |
| **Security expansion breaks legitimate workflows** | High | Medium | Configurable pattern lists; YOLO bypass env var for power users; log all blocks. |
| **Async orchestration complexity** | Medium | High | Explicitly P2; sync delegate is sufficient for 80% of use cases. |
| **SQLite bottleneck for async sessions** | Low | Medium | Session store schema designed for easy Postgres migration. |
| **Skill env-var platform limits** | Low | Medium | string.Template fallback handles invalid env names and non-bash tools. |

---

## 8. References

### Project Source (Ground Truth)
- `vibe/core/query_loop.py` — State machine, 371 lines
- `vibe/core/coordinators.py` — ToolExecutor, FeedbackCoordinator, CompactionCoordinator
- `vibe/core/context_compactor.py` — TRUNCATE/LLM_SUMMARIZE/OFFLOAD/DROP strategies
- `vibe/core/model_gateway.py` — Circuit breaker, fallback, multi-provider
- `vibe/core/error_recovery.py` — Retry with exponential backoff
- `vibe/harness/constraints.py` — HookPipeline with 5 stages
- `vibe/harness/planner.py` — Keyword-based planner
- `vibe/harness/feedback.py` — FeedbackEngine with silent degradation
- `vibe/harness/memory/trace_store.py` — SQLite + embeddings
- `vibe/harness/memory/eval_store.py` — Eval persistence
- `vibe/harness/skills/` — Parser, validator, installer, executor
- `vibe/harness/orchestration/sync_delegate.py` — Parallel subagents
- `vibe/evals/runner.py` — Eval execution engine
- `vibe/evals/multi_model_runner.py` — Scorecards
- `vibe/evals/soak_test.py` — Long-running stress tests
- `vibe/evals/observability.py` — Spans, metrics, traces

### Obsidian Reference Docs (Brainstorming Input Only)
- `Hermes Agent - Harness Design Critique.md`
- `Hermes Agent - Long-Running Agent and Sub-Agent Orchestration.md`
- `Hermes Agent - Sandbox Execution and Security Controls.md`
- `OpenClaw - Harness Design Critique.md`
- `OpenClaw - Long-Running Agents & Sub-Agent Harness.md`
- `OpenClaw - Security Preventions.md`

---

## 9. Amendment Log

| Version | Date | Changes |
|---------|------|---------|
| v1.1 | 2026-04-25 | Original plan based on live codebase audit. |
| v1.2 | 2026-04-25 | Gemini revision. Added high-confidence fast-path, efficiency metrics, Pydantic config, security spans, wiki pending/, env-var SkillExecutor. Removed file tree, metrics, effort estimates. |
| **v1.2.1** | 2026-04-25 | **This document.** Restores planner safety fallback, env-var + template hybrid for SkillExecutor, and all actionable sections (file tree, success metrics, effort estimates, risks). |

---

*Plan version: 1.2.1*  
*Next step: Break Phase 1 into GitHub issues and assign priorities.*
