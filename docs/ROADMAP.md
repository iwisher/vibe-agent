# Vibe Agent Roadmap & Plans

This document tracks the progress of Vibe Agent, from its core foundation to future platform enhancements.

## 🏁 Completed Milestones

### Foundation & Core Harness
- [x] Project scaffold and core directory structure.
- [x] **Model Gateway**: Unified async client for multi-provider support (OpenAI, Anthropic).
- [x] **Multi-Provider Support**: Configure multiple providers (OpenRouter, Anthropic, Ollama) in `config.yaml`.
- [x] **Cross-Provider Fallback**: Intelligent fallback across different providers and API formats.
- [x] **Circuit Breaker**: Resilience against failing model endpoints.
- [x] **Tool System**: Integrated Bash and File management tools with security jailing.
- [x] **Query Loop**: Robust state machine managing the agent's thought/action cycle.
- [x] **Context Compaction**: Automated token budget management with summarization fallback.
- [x] **Error Recovery**: Exponential backoff with jitter for transient failures.

### Security & Hardening
- [x] BashTool shell injection protection (using `exec` instead of `shell`).
- [x] File tool path traversal protection (using realpath + jail checks).
- [x] Secure handling of API keys (no hardcoded keys, env var support).

### Evaluation & Quality
- [x] End-to-end eval runner (`run_e2e_evals.py`).
- [x] 30+ built-in eval cases covering file ops, bash, and reasoning.
- [x] Model benchmarking infrastructure for comparing different LLMs.

---

## 🏗️ In Progress (Phase 1.x)

- [ ] **Observability Hardening**: End-to-end trace validation and metrics aggregation.
- [ ] **Wiki Memory**: Minimal markdown-based long-term memory.
- [ ] **QueryLoop Cancellation Safety**: Robust cleanup on task cancellation.
- [ ] **Advanced Token Estimation**: Integration with `tiktoken` for accurate counts.

---

## 🚀 Future Roadmap (Phase 2+)

### Async Engine & Platform
- [ ] **Async Session Orchestration**: Support for long-lived, steerable async sessions.
- [ ] **Topology Planner**: Intelligent logic for synchronous vs. asynchronous execution.
- [ ] **FastAPI Server**: REST and WebSocket API for external integrations.
- [ ] **React Dashboard**: Visual interface for trace viewing and session monitoring.

### Intelligence & Optimization
- [ ] **Auto-Harness Optimizer**: Autonomous hill-climbing to improve agent performance.
- [ ] **MCP Bridge Expansion**: Enhanced support for Model Context Protocol servers.
- [ ] **Cost-Aware Routing**: Automatically choose the cheapest capable model for a task.

---

*Last updated: April 19, 2026*
