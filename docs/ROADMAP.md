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

## 🧠 Architectural Critique

At the conclusion of Phase 2, the system's architecture demonstrates strong resilience and sandbox security, but also highlights areas for growth:
- **Harness & QueryLoop**: The decoupling of the monolithic QueryLoop into distinct Coordinators has been successful for testability. However, the linear iteration limit (max 50) remains rigid. It lacks native DAG-based task execution and background task suspension (e.g., waiting on human-in-the-loop input).
- **Skill System**: The v2 markdown-based skill system (`SKILL.md`) is highly secure and portable. But variable substitution currently relies on basic string templating; it requires more structured argument parsing and validation.
- **Memory (Tripartite)**: The foundation is solid (Wiki + Extractor + RLM Analyzer) and properly utilizes parallel asynchronous fetches. However, the `RLMThresholdAnalyzer` only tracks telemetry and doesn't actually trigger the physical training pipeline yet (Phase 3b deferred). Additionally, `fastText` is fast but lacks the deeper semantic understanding of a modern lightweight transformer.
- **Security**: The sandboxing and path jailing are robust, but for complex, multi-file code refactoring, the system lacks automated rollback capabilities (such as shadow git workspaces) to undo destructive operations seamlessly.

---

## 🚀 Top 10 Next Steps (Phase 3+)

1. **Real-time Vector Search Upgrade**: Migrate from `fastText` to a lightweight local transformer (e.g., `all-MiniLM-L6-v2`) for richer semantic routing in the PageIndex without relying on external APIs.
2. **Phase 3b RLM Training Pipeline**: Implement the automated LoRA fine-tuning and distillation pipeline that is triggered by the `RLMThresholdAnalyzer`.
3. **Agentic "Steerable" Sessions**: Upgrade the Query Loop to support async suspension, human-in-the-loop branching, and background persistence (spawn/steer/kill primitives).
4. **Topology Task Planner**: Evolve the `ContextPlanner` to build Directed Acyclic Graphs (DAGs) of sync/async tasks (spawning parallel sub-agents) instead of just sequential tool selection.
5. **Advanced MCP Bridge Expansion**: Broaden Model Context Protocol support to handle dynamic resource discovery, pagination, and OAuth authentication flows.
6. **Cost-Aware Dynamic Routing**: Implement a dynamic router that selects the cheapest capable model from the `ProviderRegistry` based on the prompt's estimated complexity.
7. **React-Based Trace Dashboard**: Build a visual Web UI (FastAPI + React) to inspect session traces, wiki memory graphs, and skill execution logs in real-time.
8. **Multi-Agent Swarm Orchestration**: Extend the harness to allow multiple specialized Vibe Agents to communicate and delegate sub-tasks to one another.
9. **Autonomous Skill Generation (Skill-Maker)**: Create a native pipeline that allows the agent to write, test, sandbox, and install its own `SKILL.md` files autonomously based on recurring tasks.
10. **Shadow Workspace Rollbacks**: Implement hidden git-based file checkpointing to automatically rollback the workspace if the agent fails a complex refactoring task.

---

*Last updated: 2026-04-26*
