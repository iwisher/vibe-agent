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
- [x] 30+ built-in eval cases covering file ops, bash, reasoning, security.
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

### Phase 2d: Memory & CLI
- [x] Unified embedding layer using fastText (50-dim, 5MB).
- [x] Optimized TraceStore persistence with `numpy` float32 serialization.
- [x] Secret Redaction with 9+ security patterns (OpenAI, AWS, GitHub, etc.).
- [x] Hybrid Semantic Planner with keyword fast-path and embedding fallback.
- [x] SQLite trace store with automated session logging and UUID tracking.
- [x] SQLite eval store with schema migrations.
- [x] Wiki memory (minimal markdown-based read/write).
- [x] Interactive CLI with readline history and real-time token metrics.
- [x] Skill management CLI (`vibe skill create/validate/install/list/run/uninstall`).

### Phase 2e: Tripartite Memory System
- [x] Phase 1b: Async background knowledge extraction with parallelized novelty scoring.
- [x] Phase 2: RLM Threshold Analyzer MVP for telemetry-driven token and compaction tracking.
- [x] Phase 3: FlashLLM Contradiction Detection and Quality Gates.
- [x] Concurrent async fetch optimization (`asyncio.gather`) across wiki read loops.
- [x] Memory CLI (`vibe memory status`, `vibe wiki expire`).
- [ ] Phase 3b: Real Recursive Language Model training (Deferred).

---

## 🏗️ In Progress (Phase 2 Hardening)

- [ ] **Factory-per-case EvalRunner**: Fresh QueryLoop per case to prevent state bleed.
- [ ] **Structured FeedbackEngine**: `FeedbackStatus` enum to distinguish failure modes from neutral scores.
- [ ] **Safe SkillExecutor**: Env-var passing primary, `string.Template` fallback.
- [ ] **Real LLM Summarization**: Wire `ContextCompactor` to loop's LLM client with efficiency metrics.
- [ ] **Security Expansion**: 5-layer defense model + Pydantic config validation.
- [ ] **Wiki Compiler**: Nightly trace compilation with `pending/` human review mechanism.

---

## 🚀 Future Roadmap (Phase 3+)

### Advanced Orchestration
- [ ] **Budget Governance**: `IterationBudget` + `BudgetConfig` for turn and token caps.
- [ ] **Checkpointing**: Shadow git snapshots for filesystem rollback.
- [ ] **Async Session Orchestration**: spawn/steer/kill primitives.
- [ ] **TaskFlows**: Managed iterative workflows with block/resume/retry.
- [ ] **Topology Planner**: Intelligent sync vs. async execution decisions.

### Platform & API
- [ ] **FastAPI Server**: REST and WebSocket API for external integrations.
- [ ] **React Dashboard**: Visual interface for trace viewing and session monitoring.

### Intelligence & Optimization
- [ ] **Auto-Harness Optimizer**: `vibe optimize` for autonomous hill-climbing.
- [ ] **Trace Mining**: Auto-generate eval candidates from failure patterns.
- [ ] **MCP Bridge Expansion**: Enhanced Model Context Protocol server support.
- [ ] **Cost-Aware Routing**: Automatically choose cheapest capable model for a task.

---

*Last updated: 2026-04-26*
