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

---

## 🔮 Phase 4: Recursive Self-Improvement

### 4.1 RLM Training Pipeline
- [ ] LoRA fine-tuning pipeline triggered by `RLMThresholdAnalyzer`
- [ ] Use `unsloth` or `llama.cpp` for local quantized training
- [ ] Write fine-tuned weights to `rlm_model_path`
- [ ] A/B test fine-tuned vs base model on eval suite

### 4.2 Autonomous Skill Generation (Skill-Maker)
- [ ] `SkillMakerPipeline` detecting recurring task patterns from wiki
- [ ] LLM-generated `SKILL.md` drafts
- [ ] Sandbox validation and approval gate

### 4.3 Multi-Agent Swarm Orchestration
- [ ] `SwarmOrchestrator` spawning specialized sub-agents
- [ ] `AgentProtocol` message bus via `asyncio.Queue`
- [ ] Shared wiki across swarm members

---

## 🖥️ Phase 5: Observability & Ecosystem

### 5.1 React Trace Dashboard
- [ ] FastAPI backend serving trace data
- [ ] React frontend: session timeline, wiki graph, skill waterfall, telemetry
- [ ] CLI: `vibe dashboard` to launch

### 5.2 Shadow Workspace Rollbacks
- [ ] Hidden git branch `vibe/shadow-<session-id>` before write-heavy tasks
- [ ] `vibe rollback` to restore on ERROR/INCOMPLETE

### 5.3 CI/CD Integration
- [ ] GitHub Action for eval suite with regression gate
- [ ] Scorecard publishing to PR comments

---

---

## 🧠 Architectural Critique

A candid review of the current system's strengths and gaps across all key components.

### Harness & Query Loop

**Strengths:**
- The 9-state `QueryState` machine cleanly separates concerns (PLANNING → TOOL_EXECUTION → SYNTHESIZING).
- Decoupling into `ToolExecutor`, `FeedbackCoordinator`, and `CompactionCoordinator` improved testability significantly.
- Background task pattern (`asyncio.create_task`) for wiki extraction and RLM analysis correctly avoids blocking user responses.

**Gaps:**
- `max_iterations=50` is a hard linear limit with no adaptive behavior. Long multi-step agentic tasks (e.g., refactoring a codebase) need progressive depth budgets or human-in-the-loop checkpoints.
- The loop has no native concept of **parallel sub-tasks**. All tool calls are serial within a single session.
- No session **suspension/resumption** — if the process dies, all in-flight work is lost. There is no durable execution state.
- `_find_existing_page` currently uses simple title-overlap (Jaccard similarity). For semantic deduplication, vector similarity is needed.

### Skill System

**Strengths:**
- The TOML-frontmatter + Markdown body format is human-readable and version-control friendly.
- 80+ security scanning patterns catch the most common injection attack vectors.
- Atomic installation with rollback prevents partial installs from leaving the system in a bad state.

**Gaps:**
- Variable substitution is string-based (`{variable}` replacement). No type coercion, no default values, no schema validation for skill inputs.
- Skills cannot `await` other skills or spawn sub-agents — they are strictly sequential bash-step executors.
- There is no skill **marketplace** or discovery mechanism beyond local path/git install.
- Skills cannot dynamically declare new tools — they are constrained to the harness's registered tool set.

### Tripartite Memory System

**Strengths:**
- The LLMWiki + PageIndex + TelemetryCollector three-layer architecture is architecturally sound.
- Async background extraction with `asyncio.create_task` correctly avoids adding latency to user-facing queries.
- The FlashLLM contradiction gate catches factual conflicts before pages are promoted to `verified`.
- Novelty scoring via BM25 title-overlap prevents near-duplicate knowledge proliferation.

**Gaps:**
- `RLMThresholdAnalyzer` only **logs** a trigger decision — it doesn't actually fine-tune a model (Phase 3b deferred). The "R" in RLM is aspirational.
- `PageIndex` uses fastText (50-dim keyword vectors). It lacks the deep semantic understanding of a modern transformer (e.g., `all-MiniLM-L6-v2`), leading to lower recall for paraphrase queries.
- Wiki pages are stored as flat `.md` files. There is no graph database or entity resolution — two pages about the same concept with different names won't be linked unless the LLM uses `[[slug]]` syntax exactly.
- The novelty threshold (`0.5` default) is a single global value. A per-tag or per-domain threshold would be more nuanced (e.g., stricter for `finance`, looser for `general`).
- `memory_status` CLI accesses `wiki.db.conn` directly — tight coupling that bypasses the `TelemetryCollector` abstraction.

### Model Gateway & Resilience

**Strengths:**
- Circuit breaker per model with configurable threshold and cooldown is production-grade.
- Adapter pattern (OpenAI/Anthropic) makes adding new providers straightforward.

**Gaps:**
- No **cost tracking** — the gateway doesn't log token costs or enforce spend limits.
- No **latency-aware routing** — the fallback chain is static (defined in config), not dynamic based on observed p99 latency.
- The `FlashLLMClient` has a separate code path from the main `LLMClient` with no shared circuit breaker. A flash model failure doesn't feed into the main fallback chain.

### Evaluation Suite

**Strengths:**
- 50+ built-in eval cases with subsystem tags and difficulty levels.
- Baseline scorecard regression detection (must stay within 5% of `docs/baseline_scorecard.json`).
- Soak testing infrastructure with configurable cases-per-minute.

**Gaps:**
- `EvalRunner` reuses a single `QueryLoop` across all cases — state bleed between runs can cause false failures.
- No **adversarial evals** — there are no prompt injection, jailbreak, or data exfiltration test cases.
- Eval results are only stored locally. No CI/CD integration or dashboard visualization.

---

## 🚀 Top 10 Next Steps (Phase 3+)

Prioritized by impact × effort, based on the architectural critique above.

### 1. 🔍 Vector Search Upgrade (PageIndex)
**Problem**: fastText 50-dim vectors have poor recall for paraphrase queries.  
**Solution**: Replace fastText with `sentence-transformers` (`all-MiniLM-L6-v2`, ~22MB). Wrap behind a `VectorIndex` protocol so the swap is transparent to callers.  
**Impact**: Dramatically better wiki retrieval relevance for the knowledge extractor and planner.

### 2. 🧬 Phase 3b RLM Training Pipeline
**Problem**: `RLMThresholdAnalyzer` logs a trigger decision but never acts on it.  
**Solution**: Implement automated LoRA fine-tuning pipeline triggered by the analyzer. Use `unsloth` or `llama.cpp` for local quantized training. Write fine-tuned weights to `rlm_model_path`.  
**Impact**: The agent improves from its own conversation history — true closed-loop learning.

### 3. ⏸️ Durable Session Suspension & Resumption
**Problem**: If the process dies mid-task, all work is lost.  
**Solution**: Serialize `QueryLoop.messages` + `QueryState` to SQLite (extend `TraceStore`) on every state transition. On startup, offer to resume the last incomplete session.  
**Impact**: Reliability for long multi-hour agentic tasks.

### 4. 🌐 DAG-Based Task Planner (Parallel Sub-Tasks)
**Problem**: All tool calls are serial. Multi-file refactoring, concurrent web scraping, and parallel research are bottlenecked.  
**Solution**: Evolve `ContextPlanner` to output a DAG of tasks. Wire `asyncio.gather` at the `ToolExecutor` level to run independent DAG nodes concurrently.  
**Impact**: 5–10× speedup on parallelizable agentic tasks.

### 5. 💰 Cost-Aware Dynamic Routing
**Problem**: Fallback chain is static. An expensive frontier model is always chosen first, even for simple queries.  
**Solution**: Add a `CostRouter` that estimates prompt complexity (token count + tool use), then selects the cheapest model in `ProviderRegistry` that is capable. Track cumulative spend per session.  
**Impact**: 3–5× cost reduction on mixed-complexity workloads.

### 6. 🏗️ Factory-per-Case EvalRunner
**Problem**: Single `QueryLoop` reuse causes state bleed between eval cases.  
**Solution**: Instantiate a fresh `QueryLoop` (via `QueryLoopFactory`) for each eval case. Run cases concurrently with `asyncio.gather` for 4–8× speedup.  
**Impact**: Eliminates false failures in eval suite; faster feedback loop during development.

### 7. 🖥️ React Trace Dashboard
**Problem**: Session traces, wiki graphs, and skill logs are only inspectable via CLI.  
**Solution**: Build a `FastAPI` + `React` web UI. Serve from `vibe dashboard`. Display: session timeline, wiki knowledge graph (D3.js), skill execution waterfall, telemetry charts.  
**Impact**: Makes the agent's reasoning and memory observable — critical for debugging and demos.

### 8. 🤖 Multi-Agent Swarm Orchestration
**Problem**: A single Vibe Agent instance handles all tasks. There is no delegation.  
**Solution**: Add a `SwarmOrchestrator` that spawns specialized sub-agents (e.g., a "Research Agent" + "Coding Agent" + "Critic Agent") with a shared wiki. Implement an `AgentProtocol` message bus using `asyncio.Queue`.  
**Impact**: Unlocks complex workflows that benefit from role specialization.

### 9. 🛠️ Autonomous Skill Generation (Skill-Maker)
**Problem**: Skills are written by humans. The agent cannot learn new reusable automations.  
**Solution**: Create a `SkillMakerPipeline` that: (1) detects recurring task patterns from wiki extractions, (2) generates a `SKILL.md` draft using the LLM, (3) sandboxes and validates it, (4) proposes installation via the approval gate.  
**Impact**: The agent becomes self-improving — new capabilities emerge from usage patterns.

### 10. ↩️ Shadow Workspace Rollbacks
**Problem**: Complex file refactoring by the agent can leave the workspace in a broken state with no easy undo.  
**Solution**: Before any write-heavy task, create a hidden git branch (`vibe/shadow-<session-id>`). If the task fails (state = ERROR/INCOMPLETE), offer `vibe rollback` to restore the workspace.  
**Impact**: Removes fear of running the agent on real codebases — critical for adoption.

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

*Last updated: 2026-05-02 | Test suite: **948 tests collected, 948 passing***
