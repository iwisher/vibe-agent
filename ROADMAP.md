# Vibe Agent — Roadmap

> **Vision:** An open agent harness platform that treats the harness as the product, combining Hermes-style synchronous delegation with OpenClaw-style async orchestration, and closes the loop with eval-driven hill-climbing.

---

## Phase 1: Core Harness + CLI + Evals (Current)

**Goal:** A working CLI agent (`vibe`) that can edit code, run bash, manage context, and execute a built-in optimization eval suite.

### Milestone 1.1 — Foundation (Completed)
- [x] Project scaffold (`pyproject.toml`, directory structure)
- [x] Model Gateway (`vibe/core/model_gateway.py`)
- [x] Error Recovery with exponential backoff + jitter
- [x] Tool System + Bash/File tools
- [x] Query Loop with context compaction
- [x] SQLite Trace Store
- [x] Eval Store + 3 built-in YAML evals
- [x] Sync Delegate (3 parallel workers)
- [x] CLI entry point (`vibe`)

### Milestone 1.2 — Harness Hardening (In Progress)
- [x] Remove hardcoded API keys from model gateway and CLI
- [x] Harden BashTool with regex-based denylist + whitelist mode
- [x] Add HookPipeline to Query Loop (PRE/POST constraint stages)
- [x] Add explicit QueryState machine to Query Loop
- [ ] Build `vibe/harness/instructions.py` (AGENTS.md + skill loader)
- [ ] Build `vibe/harness/feedback.py` (independent evaluator loop)
- [ ] Expand built-in eval suite to 10 cases
- [ ] Build eval runner CLI (`vibe eval run`)

### Milestone 1.3 — Testing & Observability
- [ ] Comprehensive unit + integration test coverage (>80% core)
- [ ] Trace viewer CLI (`vibe traces`)
- [ ] Wiki memory (minimal markdown-based)

---

## Phase 2: Async Orchestration + Platform (Deferred)

**Goal:** Turn the CLI into a true platform with async sessions, dashboard, and auto-optimization.

### Milestone 2.1 — Async Engine
- [ ] Async session orchestration (`vibe/harness/orchestration/async_sessions.py`)
- [ ] Steerable subagents (mid-flight messages)
- [ ] Topology Planner (sync vs async decision logic)
- [ ] Parent state machine formalization

### Milestone 2.2 — API + Dashboard
- [ ] FastAPI server with REST + WebSocket
- [ ] React dashboard (trace viewer, eval runner, session monitor)
- [ ] MCP bridge for external tool servers

### Milestone 2.3 — Auto-Harness Optimizer
- [ ] `vibe optimize` command (autonomous harness hill-climbing)
- [ ] Trace mining for eval candidate generation
- [ ] Holdout set validation to prevent overfitting

---

## Design Principles

1. **Port, don’t rewrite** — Reuse proven patterns from `claude-code-clone`.
2. **Test-driven evals** — Every eval is a regression test.
3. **Thin interfaces, fat harness** — CLI/API are wrappers around the harness core.
4. **No hidden magic** — Every behavior is inspectable and overridable.

---

*Last updated: 2026-04-15*
