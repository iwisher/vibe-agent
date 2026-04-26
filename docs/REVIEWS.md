# Code Reviews & Critiques

This document archives historical code reviews and architectural critiques that have shaped the development of Vibe Agent.

---

## [2026-04-25] Harness Design Critique & Improvement Plan (Kimi CLI)

### Summary
Comprehensive audit of the harness stack (`constraints`, `feedback`, `planner`, `instructions`, `memory`, `orchestration`, `skills`, `evals/runner`) against live source code. Identified 5 P0 issues and 7 P1/P2 gaps.

### Key Findings & Actions
- **ContextPlanner:** Naive keyword matching with self-defeating "return all tools" fallback.
  - *Action:* Hybrid semantic planner (keyword → embedding → LLM router) with high-confidence fast-path.
- **TraceStore:** O(N) in-memory vector scan using `pickle`.
  - *Action:* Random-projection signatures in SQLite + raw float32 blobs.
- **EvalRunner:** Shared QueryLoop state pollution between cases.
  - *Action:* Factory-per-case isolation.
- **FeedbackEngine:** Bare `except Exception:` destroys measurement signal.
  - *Action:* Structured `FeedbackStatus` enum with per-mode retry logic.
- **SkillExecutor:** Naive string `.replace()` with partial-match bugs.
  - *Action:* Environment variable passing primary, `string.Template` fallback.
- **ContextCompactor:** Placeholder summaries by default.
  - *Action:* Wire LLM summarization with compaction efficiency metrics.
- **Security:** Only 2 hooks with ~4 patterns.
  - *Action:* 5-layer defense model + Pydantic config validation.

### Deliverables
- `harness-design-critique.md` (5 issues, 15 options)
- `harness-improving-plan-v1.2.1.md` (4 phases, 10 improvement areas)
- Updated `DESIGN.md` v2.0 and `DESIGN_REVIEW.md` v2.0 with actual code behaviors

---

## [2026-04-18] Independent Architecture Critique (Claude Code)

### Summary
A comprehensive review of the "Phase 5" decoupling state. Identified several critical security and stability gaps.

### Key Findings & Actions
- **Security:** Identified potential path traversal in `_redirect_path` and TOCTOU issues in `skill_manage.py`.
  - *Action:* Fixed by implementing realpath validation and ensuring resolved paths are used for all I/O.
- **Stability:** Noted `AsyncMock` misuses in tests and `QueryLoop` cancellation safety issues.
  - *Action:* Fixed `httpx` mocking in unit tests and added `try/finally` blocks to the query loop.
- **Refactoring:** Recommended extracting coordinators from the monolithic `QueryLoop`.
  - *Action:* Decomposed `QueryLoop` into `ToolExecutor`, `FeedbackCoordinator`, and `CompactionCoordinator`.

---

## [2026-04-18] Comprehensive System Review (Kimi CLI)

### Summary
Focused on tool safety, multi-provider extensibility, and dependency management.

### Key Findings & Actions
- **Vendor Lock-in:** Identified hardcoded Applesay URLs.
  - *Action:* Replaced with neutral Ollama defaults (`http://localhost:11434`).
- **Tool Jailing:** Recommended stricter validation for Bash and File tools.
  - *Action:* Implemented three-layer defense for Bash and `_resolve_and_jail` for File tools.
- **Dependency Bloat:** Suggested moving FastAPI/uvicorn to optional dependencies.
  - *Action:* Updated `pyproject.toml` to include an `[api]` extra.

---

*Refer to the full `CODE_REVIEW.md` and `CODE_REVIEW_KIMI.md` files for granular details if needed (to be archived).*

*Last updated: 2026-04-25*
