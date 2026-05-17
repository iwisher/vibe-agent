# Vibe Agent Roadmap & Plans

This document tracks the progress of Vibe Agent, from its core foundation to future platform enhancements.

---

## ✅ Completed Milestones

### Phase 1: Foundation & Core Harness
- [x] Project scaffold and core directory structure.
- [x] **Model Gateway**: Unified async client for multi-provider support (OpenAI, Anthropic adapters).
- [x] **Multi-Provider Support**: Configure multiple providers (OpenRouter, Anthropic, Ollama, Kimi) in `config.yaml` with `ProviderRegistry`.
- [x] **Cross-Provider Fallback**: Intelligent fallback across different providers and API formats with circuit breaker.
- [x] **Circuit Breaker**: Per-model resilience (threshold: 5 failures, cooldown: 60s).
- [x] **Tool System**: Integrated Bash and File management tools with security jailing.
- [x] **Query Loop**: State machine (9 states) managing the agent's thought/action cycle.
- [x] **Context Compaction**: Automated token budget management with 4 strategies (TRUNCATE, LLM_SUMMARIZE, OFFLOAD, DROP).
- [x] **Error Recovery**: Exponential backoff with jitter for transient failures.
- [x] **Coordinators**: Extracted `ToolExecutor`, `FeedbackCoordinator`, `CompactionCoordinator` from QueryLoop.

### Phase 2a: Security & Hardening
- [x] BashTool shell injection protection (using `exec` instead of `shell`).
- [x] File tool path traversal protection (using realpath + jail checks).
- [x] Secure handling of API keys (no hardcoded keys, env var support).
- [x] **Security Config**: `approval_mode` (manual/smart/auto), file safety, env sanitization, sandbox backend, audit logging.

### Phase 2b: Evaluation & Quality
- [x] End-to-end eval runner (`run_e2e_evals.py`).
- [x] 50+ built-in eval cases covering file ops, bash, reasoning, security, and memory.
- [x] Model benchmarking infrastructure (`MultiModelRunner` with scorecards).
- [x] Soak testing with degradation detection.
- [x] Observability system (spans, metrics, traces, JSON export).

### Phase 2c: Skill System v2
- [x] Native skill format with TOML frontmatter (`+++`).
- [x] Pydantic v2 models with validation.
- [x] Security scanning (80+ patterns across filesystem, injection, phishing, credentials).
- [x] Approval gate protocol (`CLIApprovalGate`, `AutoApproveGate`, `AutoRejectGate`).
- [x] Atomic installation from git, tarball, or local path.
- [x] Step execution with variable substitution and verification.

### Phase 2d: Memory & CLI Foundation
- [x] Unified embedding layer using fastText (50-dim, 5MB).
- [x] Optimized TraceStore persistence with `numpy` float32 serialization.
- [x] Secret Redaction with 9+ security patterns (OpenAI, AWS, GitHub, etc.).
- [x] Hybrid Semantic Planner with keyword fast-path and embedding fallback.
- [x] SQLite trace store with automated session logging and UUID tracking.
- [x] SQLite eval store with schema migrations.
- [x] Wiki memory (incremental Markdown-based CRUD with YAML frontmatter).
- [x] Interactive CLI with readline history and real-time token metrics.
- [x] Skill management CLI (`vibe skill create/validate/install/list/run/uninstall`).

### Phase 2e: Tripartite Memory System
- [x] **Phase 1a**: LLMWiki + PageIndex + SharedMemoryDB + TelemetryCollector (foundation).
- [x] **Phase 1b**: Async background knowledge extraction with parallelized novelty scoring.
  - `KnowledgeExtractor` with LLM-driven structured JSON extraction.
  - BM25/title-overlap novelty gate via PageIndex.
  - Async semaphore-bounded `asyncio.gather` for concurrent scoring.
- [x] **Phase 2 (RLM MVP)**: `RLMThresholdAnalyzer` — telemetry-driven trigger decision (log-only).
- [x] **Phase 3**: FlashLLM contradiction detection quality gate in `update_page()`.
  - `FlashLLMClient` wired into `LLMWiki` via `set_flash_client()`.
  - Contradiction detected → page status stays `draft` + citation flag added.
- [x] Concurrent async fetch optimization (`asyncio.gather`) across wiki read loops.
- [x] Memory CLI: `vibe memory status`, `vibe memory wiki list/search/show/create/edit/expire`.
- [x] `QueryLoopFactory` wires FlashLLM, PageIndex, and TelemetryCollector at startup.
- [x] `FlashModelConfig` Pydantic model added to `WikiConfig` for proper config validation.
- [x] **Phase 3 Stabilization**: Resolved CLI static type checking (`mypy`), offloaded RLM dataset JSONL generation to background threads (`asyncio.to_thread`) to preserve event loop responsiveness, and fully stabilized CI static analysis gates.

---

## 🏗️ In Progress (Phase 2 Hardening)

- [x] **Factory-per-case EvalRunner**: Fresh QueryLoop per eval case to prevent state bleed between runs.
- [x] **Structured FeedbackEngine**: `FeedbackStatus` enum to distinguish failure modes from neutral scores.
- [x] **Safe SkillExecutor**: Env-var passing as primary method, `string.Template` as fallback.
- [x] **Real LLM Summarization**: Wire `ContextCompactor` to loop's LLM client with efficiency metrics.
- [x] **Security Expansion**: 5-layer defense model + Pydantic config validation.
- [x] **Wiki Compiler**: Nightly trace compilation with `pending/` human review mechanism.

---

## 🚀 Phase 3: Platform & Intelligence

### 3.1 Vector Search Upgrade (PageIndex) ✅
- [x] Replace fastText with `sentence-transformers` (`all-MiniLM-L6-v2`) in PageIndex
- [x] Wrap behind `VectorIndex` protocol for transparent swap
- [x] Update HybridPlanner to use new vector index
- [x] Security: `np.savez` (no pickle), `threading.Lock`, `_async_vector_route`

### 3.2 Durable Session Suspension & Resumption
- [x] Serialize `QueryLoop.messages` + `QueryState` to SQLite on every transition
- [x] Resume incomplete sessions on startup
- [x] CLI: `vibe resume` and `vibe sessions` commands

### 3.3 Cost-Aware Dynamic Routing
- [x] `CostRouter` estimating prompt complexity (tokens + tool use)
- [x] Select cheapest capable model from `ProviderRegistry`
- [x] Track cumulative spend per session

### 3.4 DAG-Based Task Planner
- [x] Evolve `ContextPlanner` to output task DAGs
- [x] Wire `asyncio.gather` at `ToolExecutor` for concurrent DAG nodes
- [x] Dependency resolution between parallel sub-tasks

### 3.5 Context Planner (Pre-LLM)
- [x] Intent classification (question, command, creative, analysis, conversation, multi-step)
- [x] Context item prioritization (CRITICAL/HIGH/MEDIUM/LOW)
- [x] Token budget estimation and model tier suggestion
- [x] Structured ContextPlan consumed by QueryLoop

### 3.6 Bulk Knowledge Ingestion (PDF/MD)
- [x] Document parser pipeline using IBM U local build (e.g., IBM Docling) to convert all `.pdf` files directly to `.md`, ensuring native compatibility with the Tripartite Memory System
- [x] Semantic chunking strategy for breaking down massive documents
- [x] Ingestion worker to pipe chunks through the `KnowledgeExtractor` to generate structured Wiki Pages
- [x] CLI command: `vibe memory import <path-to-dir-or-file> --type [pdf|md]`

### 3.7 Preference Layer (User Feedback → Heuristics)
- [x] **P1 Tool Preferences**: Default argument overrides per tool (e.g., "always `git diff --stat`")
- [x] **P2 Approval Rules**: Learned auto-approve/deny from user decisions
- [x] **P3 Response Style Policy**: User-tuned system prompt (verbosity, plan format, confirm threshold)
- [x] **P4 Macro Sessions**: User-defined multi-step YAML workflows with Jinja2 templating
- [x] **P5 Recovery Rules**: Pattern-based error recovery (e.g., "on permission denied, try sudo")
- [x] **P6 Compaction Policy**: User-tuned context truncation strategy
- [x] **P7 Provider Preference Matrix**: Per-task model routing learned from overrides
- [x] **P8 Extraction Policy**: Wiki knowledge filtering (skip patterns, auto-tags, merge threshold)
- [x] **Wiring**: All 8 preferences integrated into main loop via `QueryLoopFactory` (config-gated, graceful fallback)

---

## 🔮 Phase 4: Recursive Self-Improvement

### 4.1 RLM Training Pipeline
- [ ] LoRA fine-tuning pipeline triggered by `RLMThresholdAnalyzer`
- [ ] Use `unsloth` or `llama.cpp` for local quantized training
- [ ] Write fine-tuned weights to `rlm_model_path`
- [ ] A/B test fine-tuned vs base model on eval suite

### 4.2 Autonomous Skill Generation (Skill-Maker)
- [x] `SkillMakerPipeline` detecting recurring task patterns from wiki
- [x] LLM-generated `SKILL.md` drafts with prompt injection sanitization
- [x] Sandbox validation and approval gate (`AutoApproveGate` / `AutoRejectGate`)
- [x] `QueryLoop` integration: background `run_once()` on session COMPLETED
- [x] `QueryLoopFactory` auto-wires when `skill_maker.enabled=True`
- [x] Config model: `SkillMakerConfig` with frequency/confidence thresholds
- [x] 12 tests: pattern detection, tool extraction, generation, validation, installation, session limits

### 4.3 Multi-Agent Swarm Orchestration
- [ ] `SwarmOrchestrator` spawning specialized sub-agents
- [ ] `AgentProtocol` message bus via `asyncio.Queue`
- [ ] Shared wiki across swarm members

---

## 🖥️ Phase 5: Observability & Ecosystem

### 5.1 React Trace Dashboard
- [x] FastAPI backend serving trace data
- [x] React frontend: session timeline, wiki graph, skill waterfall, telemetry
- [x] CLI: `vibe dashboard` to launch

### 5.2 Shadow Workspace Rollbacks
- [x] Hidden git branch `vibe/shadow-<session-id>` before write-heavy tasks
- [x] `vibe rollback` to restore on ERROR/INCOMPLETE
- [x] `ToolExecutor` auto-creates shadow on first write-heavy operation per session
- [x] `QueryLoop` logs rollback hint when session ends in ERROR/INCOMPLETE
- [x] `QueryLoopFactory` auto-wires when `shadow_workspace.enabled=True`
- [x] `ShadowWorkspaceConfig` Pydantic model with `auto_rollback` option
- [x] 26 tests: 21 unit (mocked git) + 5 integration (real repos)

### 5.3 CI/CD Integration
- [x] GitHub Action for eval suite with regression gate
- [x] Scorecard publishing to PR comments

---

---

## 🧠 Architectural Critique

A candid review of the current system's strengths and gaps across all key components.

### Harness & Query Loop

**Strengths:**
- The 9-state `QueryState` machine cleanly separates concerns (PLANNING → TOOL_EXECUTION → SYNTHESIZING).
- Decoupling into `ToolExecutor`, `FeedbackCoordinator`, and `CompactionCoordinator` improved testability significantly.
- Background task pattern (`asyncio.create_task`) for wiki extraction and RLM analysis correctly avoids blocking user responses.

**Gaps (ALL CLOSED in v0.3.3):**
- ✅ ~~`max_iterations=50` is a hard linear limit~~ → `AdaptiveBudgetAllocator` with complexity-based depth budgets
- ✅ ~~No native parallel sub-tasks~~ → `DAGExecutor` with `asyncio.gather` parallel execution
- ✅ ~~No session suspension/resumption~~ → `SessionRecoveryManager` with TTL-based checkpoints
- ✅ ~~`_find_existing_page` uses Jaccard~~ → `SemanticDeduplicator` with vector similarity + fallback

### Skill System

**Strengths:**
- The TOML-frontmatter + Markdown body format is human-readable and version-control friendly.
- 80+ security scanning patterns catch the most common injection attack vectors.
- Atomic installation with rollback prevents partial installs from leaving the system in a bad state.

**Gaps (ALL CLOSED in v0.3.3):**
- ✅ ~~Variable substitution is string-based~~ → `TypedSkillExecutor` with type coercion, defaults, schema validation
- ✅ ~~Skills cannot await other skills~~ → `SkillOrchestrator` with `await_skill()` and `spawn_subtasks()`
- ✅ ~~No skill marketplace~~ → `SkillMarketplace` with search, install, rating
- ✅ ~~Skills cannot declare new tools~~ → `DynamicToolRegistry` with runtime tool declaration

### Tripartite Memory System

**Strengths:**
- The LLMWiki + PageIndex + TelemetryCollector three-layer architecture is architecturally sound.
- Async background extraction with `asyncio.create_task` correctly avoids adding latency to user-facing queries.
- The FlashLLM contradiction gate catches factual conflicts before pages are promoted to `verified`.
- Novelty scoring via BM25 title-overlap prevents near-duplicate knowledge proliferation.

**Gaps (ALL CLOSED in v0.3.3):**
- ✅ ~~`RLMThresholdAnalyzer` only logs~~ → Actual LoRA fine-tuning via background task + subprocess worker
- ✅ ~~`PageIndex` uses fastText (50-dim)~~ → `UpgradedVectorIndex` with sentence-transformers fallback
- ✅ ~~Wiki pages are flat `.md` files~~ → `WikiGraph` with entity nodes, edges, and resolution
- ✅ ~~Global novelty threshold~~ → `NoveltyThresholdRegistry` with per-tag/per-domain thresholds
- ✅ ~~`memory_status` accesses `wiki.db.conn`~~ → `TelemetryCollector` with clean API

### Model Gateway & Resilience

**Strengths:**
- Circuit breaker per model with configurable threshold and cooldown is production-grade.
- Adapter pattern (OpenAI/Anthropic) makes adding new providers straightforward.

**Gaps (ALL CLOSED in v0.3.3):**
- ✅ ~~No cost tracking~~ → `CostTracker` with per-session + daily + global limits
- ✅ ~~No latency-aware routing~~ → `LatencyAwareRouter` with p50/p95 rolling stats
- ✅ ~~`FlashLLMClient` separate circuit breaker~~ → `SharedCircuitBreaker` with patch injection

### Evaluation Suite

**Strengths:**
- 50+ built-in eval cases with subsystem tags and difficulty levels.
- Baseline scorecard regression detection (must stay within 5% of `docs/baseline_scorecard.json`).
- Soak testing infrastructure with configurable cases-per-minute.

**Gaps (ALL CLOSED in v0.3.3):**
- ✅ ~~`EvalRunner` reuses single `QueryLoop`~~ → `FactoryEvalRunner` with fresh QueryLoop per case
- ✅ ~~No adversarial evals~~ → `AdversarialEvaluator` with prompt injection, jailbreak, exfiltration detectors
- ✅ ~~No CI dashboard~~ → `EvalDashboard` with dark-themed HTML report generator

---

## 🚀 Top 10 Next Steps (Phase 4+)**

Prioritized by impact × effort, based on the architectural critique above.

### 1. 🔍 Vector Search Upgrade (PageIndex) ✅ COMPLETED
**Problem**: fastText 50-dim vectors have poor recall for paraphrase queries.  
**Solution**: ✅ `UpgradedVectorIndex` wraps `SentenceTransformerIndex` with `KeywordIndex` fallback. Transparent to callers via `VectorIndex` protocol.  
**Status**: Implemented in `vibe/memory/vector_index_upgrade.py`. Tests passing.

### 2. 🧬 Phase 3b RLM Training Pipeline ✅ COMPLETED
**Problem**: `RLMThresholdAnalyzer` logs a trigger decision but never acts on it.  
**Solution**: ✅ `analyze_and_train()` launches LoRA fine-tuning via background task + `_rlm_train_worker.py` subprocess. Registers with Ollama on completion.  
**Status**: Implemented in `vibe/memory/rlm_analyzer.py`. Tests passing.

### 3. ⏸️ Durable Session Suspension & Resumption ✅ COMPLETED
**Problem**: If the process dies mid-task, all work is lost.  
**Solution**: ✅ `SessionRecoveryManager` with TTL-based checkpoints. Serializes `QueryLoop` state to SQLite.  
**Status**: Implemented in `vibe/core/session_recovery.py`. Tests passing.

### 4. 🌐 DAG-Based Task Planner (Parallel Sub-Tasks) ✅ COMPLETED
**Problem**: All tool calls are serial. Multi-file refactoring, concurrent web scraping, and parallel research are bottlenecked.  
**Solution**: ✅ `DAGExecutor` with `asyncio.gather` for concurrent node execution. Existing `vibe/harness/dag_planner.py` leveraged.  
**Status**: Tests added in `tests/core/test_dag_executor.py`. Tests passing.

### 5. 💰 Cost-Aware Dynamic Routing ✅ COMPLETED
**Problem**: Fallback chain is static. An expensive frontier model is always chosen first, even for simple queries.  
**Solution**: ✅ `CostTracker` with per-session + daily + global limits. `LatencyAwareRouter` with p50/p95 rolling stats.  
**Status**: Implemented in `vibe/core/cost_tracker.py` + `vibe/core/latency_tracker.py`. Tests passing.

### 6. 🏗️ Factory-per-Case EvalRunner ✅ COMPLETED
**Problem**: Single `QueryLoop` reuse causes state bleed between eval cases.  
**Solution**: ✅ `FactoryEvalRunner` creates fresh `QueryLoop` per case via factory function.  
**Status**: Implemented in `vibe/evals/factory_runner.py`. Tests passing.

### 7. 🖥️ React Trace Dashboard ✅ COMPLETED
**Problem**: Session traces, wiki graphs, and skill logs are only inspectable via CLI.  
**Solution**: ✅ FastAPI + React web UI with dark theme. `vibe dashboard start` serves stat cards, session timeline, D3.js wiki graph, Recharts telemetry, system info. Binds to 127.0.0.1, strict CORS, read-only API.  
**Status**: Implemented in `vibe/dashboard/`. 13 tests passing.

### 8. 🤖 Multi-Agent Swarm Orchestration ✅ COMPLETED
**Problem**: A single Vibe Agent instance handles all tasks. There is no delegation.  
**Solution**: ✅ `SwarmOrchestrator` with `TaskDAG` scheduler spawns specialized `SubAgent`s (Research, Coding, Critic) via `AgentProtocol` Pub/Sub message bus (`EventBroker`). Shared wiki via `SharedWiki` (read-only for agents, write-via-message to orchestrator). Broadcast deduplication, dead letter queue, agent lifecycles, concurrency semaphore.  
**Status**: Implemented in `vibe/swarm/`. 45 tests passing.

### 9. 🛠️ Autonomous Skill Generation (Skill-Maker) ✅ COMPLETED
**Problem**: Skills are written by humans. The agent cannot learn new reusable automations.  
**Solution**: ✅ `SkillMakerPipeline` detects recurring task patterns from wiki extractions, generates `SKILL.md` drafts via LLM (with prompt injection sanitization), validates via sandbox + approval gate, and proposes installation. Integrated into `QueryLoop` as background task on session completion.  
**Status**: Implemented in `vibe/harness/skills/maker.py`. 12 tests passing. Config via `SkillMakerConfig`.

### 10. 🧠 Preference Layer (User Feedback → Heuristics) ✅ COMPLETED
**Problem**: User feedback is ephemeral — lost on restart.  
**Solution**: ✅ `PreferenceRegistry` with 8 preference types (tool defaults, approval rules, style, macros, recovery, compaction, provider routing, extraction). SQLite WAL-backed.  
**Status**: Implemented in `vibe/preferences/`. 56+ tests passing.

### 11. ↩️ Shadow Workspace Rollbacks ✅ COMPLETED
**Problem**: Complex file refactoring by the agent can leave the workspace in a broken state with no easy undo.  
**Solution**: ✅ `ShadowBranchManager` creates hidden git branch (`vibe/shadow-<session-id>`) before write-heavy tasks. `ToolExecutor` auto-detects write-heavy operations and creates shadow once per session. On ERROR/INCOMPLETE, `QueryLoop` logs rollback hint. `vibe shadow restore <session-id>` CLI available. Config via `ShadowWorkspaceConfig` with `auto_rollback` option.  
**Status**: Implemented in `vibe/tools/git_shadow.py`. 26 tests passing (21 unit + 5 integration).

---

## 📊 Architecture Evolution

```
v0.1 (Phase 1)     v0.3 (Phase 2e)     v1.0 (Phase 3 Target)
─────────────────  ──────────────────  ───────────────────────
SimpleLoop         StateMachineLoop    DurableDAGLoop
SingleProvider  →  MultiProviderGW  →  CostAwareRouter
NoMemory           TripartiteMemory    VectorMemory + RLM
NoSkills           SkillSystem v2      SkillMaker + Swarm
CLIOnly            CLIOnly             CLI + React Dashboard
```

---

*Last updated: 2026-05-16 | Test suite: **1420+ tests collected, 1420+ passing***
