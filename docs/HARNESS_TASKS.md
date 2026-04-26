# Harness Implementation Task List

## Phase 1: Critical Fixes & Config (Weeks 1-2)

### P0 Tasks
- [ ] **3.1 Hybrid Semantic Planner** (3 days)
  - [ ] 3.1a: Keyword fast-path (existing, verify)
  - [ ] 3.1b: Embedding scorer with MiniLM (384-dim, local)
  - [ ] 3.1c: High-confidence fast-path (similarity > 0.8)
  - [ ] 3.1d: LLM router call (cheap model, JSON output)
  - [ ] 3.1e: Safety fallback (return all tools, log metric)
  - [ ] 3.1f: Tool embedding cache (SQLite table `tool_embeddings`)
  - [ ] 3.1g: Query cache (LRU, TTL 1hr)
  - [ ] 3.1h: Observability spans (`planner_tier_eval`)
  - [ ] 3.1i: Eval cases (`planner_semantic_*.yaml`)

- [ ] **3.2 Scalable TraceStore** (2 days)
  - [ ] 3.2a: Replace pickle with raw float32 blobs
  - [ ] 3.2b: Random-projection signatures (64-bit integer)
  - [ ] 3.2c: Hamming distance pre-filtering
  - [ ] 3.2d: Time-window default (90 days)
  - [ ] 3.2e: Schema migration (signature, updated_at, indexes)

- [ ] **3.3 Factory-Per-Case EvalRunner** (1 day)
  - [ ] 3.3a: Change EvalRunner to accept QueryLoopFactory
  - [ ] 3.3b: run_case() creates fresh loop per case
  - [ ] 3.3c: Add diff_score column migration

- [ ] **3.4 Structured FeedbackEngine** (2 days)
  - [ ] 3.4a: FeedbackStatus enum (SUCCESS, PARSE_ERROR, API_ERROR, TIMEOUT, VALIDATION_ERROR)
  - [ ] 3.4b: FeedbackResult dataclass (status, score, issues, suggested_fix, error_type)
  - [ ] 3.4c: Caller behavior in FeedbackCoordinator.evaluate()
  - [ ] 3.4d: Metrics: `feedback_attempts_total{status=...}`

- [ ] **3.5 SkillExecutor Env Var + Template Fallback** (1.5 days)
  - [ ] 3.5a: Primary approach - env var passing to BashTool
  - [ ] 3.5b: Fallback - string.Template for invalid env names
  - [ ] 3.5c: Migration support ({key} legacy, ${key} new, $key new)
  - [ ] 3.5d: SkillValidator checks for ambiguous variables

- [ ] **3.7 Pydantic Config Schema** (1 day)
  - [ ] 3.7a: SecurityConfig (already done in security phase)
  - [ ] 3.7b: BudgetConfig dataclass
  - [ ] 3.7c: MemoryConfig dataclass
  - [ ] 3.7d: Strict validation integration

## Phase 2: Reliability, Depth & Observability (Weeks 3-5)

- [ ] **3.6 Real LLM Summarization** (3 days)
  - [ ] 3.6a: Structured summarization prompt
  - [ ] 3.6b: Wire summarize_fn to LLM client
  - [ ] 3.6c: Compaction efficiency metric
  - [ ] 3.6d: Anti-thrashing fallback (efficiency < 1.5x)

- [ ] **3.7 Security Hardening Layer 1-2** (3 days)
  - [ ] 3.7a: Static analysis (regex denylist, unicode detection)
  - [ ] 3.7b: Policy engine (PermissionGate, PolicyGate, FileSafetyGate, PathTraversalGate, URLSafetyGate)
  - [ ] 3.7c: `security_hook_eval` spans
  - [ ] 3.7d: Security eval cases (`security_*.yaml`)

- [ ] **3.8 Wiki Memory Compiler** (2 days)
  - [ ] 3.8a: Compiler scans trace store
  - [ ] 3.8b: Pending directory mechanism
  - [ ] 3.8c: Human review workflow
  - [ ] 3.8d: ContextPlanner integration

- [ ] **3.9 Budget Governance** (2 days)
  - [ ] 3.9a: IterationBudget dataclass
  - [ ] 3.9b: BudgetConfig (result size caps)
  - [ ] 3.9c: Integration with QueryLoop

## Phase 3: Advanced Features (Weeks 6-8)

- [ ] **3.9 CheckpointManager** (3 days)
  - [ ] 3.9a: Shadow git repos
  - [ ] 3.9b: Auto-snapshot before writes
  - [ ] 3.9c: CLI /rollback commands
  - [ ] 3.9d: Prune to 50 snapshots

- [ ] **3.7 Smart Approval Gate** (2 days)
  - [ ] 3.7e: LLM-as-judge gate (already done in security phase - SmartApprover)
  - [ ] 3.7f: Integration with HookPipeline

- [ ] **3.10 Session Store** (2 days)
  - [ ] 3.10a: SQLite session registry
  - [ ] 3.10b: Parent-child relationships
  - [ ] 3.10c: Status tracking

- [ ] **3.10 AsyncDelegate** (4 days)
  - [ ] 3.10d: spawn (non-blocking)
  - [ ] 3.10e: steer (mid-flight)
  - [ ] 3.10f: kill (cascading interrupt)

## Phase 4: Orchestration & Hill-Climbing (Weeks 9-11)

- [ ] **3.10 TaskFlow Engine** (4 days)
  - [ ] 3.10g: TaskFlowStatus enum
  - [ ] 3.10h: run_task, set_waiting, resume, finish, fail

- [ ] **3.10 Topology Planner** (2 days)
  - [ ] 3.10i: sync vs async decision engine

- [ ] **Trace Mining for Eval Generation** (3 days)

- [ ] **Harness Optimizer (`vibe optimize`)** (4 days)

## Already Done (Security Phase)
- [x] Security hooks (file_safety, url_safety, secret_redaction, audit, smart_approval, checkpoints, skills_guard)
- [x] Pydantic SecurityConfig

## Files to Create/Modify

### New Files
- `vibe/harness/planner.py` - Hybrid semantic planner
- `vibe/harness/memory/session_store.py` - Session registry
- `vibe/harness/orchestration/async_delegate.py` - Async subagent spawner
- `vibe/harness/orchestration/task_flow.py` - Managed workflows
- `vibe/harness/orchestration/topology_planner.py` - Sync/async decision
- `vibe/core/checkpoint_manager.py` - Shadow git snapshots
- `vibe/core/budget.py` - IterationBudget + BudgetConfig

### Modified Files
- `vibe/harness/memory/trace_store.py` - Signatures, raw blobs
- `vibe/harness/memory/eval_store.py` - diff_score column
- `vibe/harness/memory/wiki.py` - Compiler, pending dirs
- `vibe/harness/feedback.py` - FeedbackStatus, structured errors
- `vibe/harness/skills/executor.py` - Env-var passing
- `vibe/harness/skills/validator.py` - Invisible unicode checks
- `vibe/core/context_compactor.py` - Real summarization
- `vibe/evals/runner.py` - Factory-per-case
- `vibe/core/config.py` - BudgetConfig, MemoryConfig

### Eval Cases
- `vibe/evals/builtin/planner_semantic_*.yaml`
- `vibe/evals/builtin/security_*.yaml`
- `vibe/evals/builtin/long_running_*.yaml`
