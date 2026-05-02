# Changelog

All notable changes to Vibe Agent will be documented in this file.

---

## [0.3.0-alpha] — 2026-04-26

### Added
- **Tripartite Memory System**:
  - **LLMWiki**: Markdown-based long-term memory with strict file locking and parallelized backlink resolution. Uses FlashLLM for contradiction detection.
  - **KnowledgeExtractor**: Asynchronous background knowledge extraction utilizing `asyncio.gather` for parallel novelty scoring and confidence gating.
  - **RLMThresholdAnalyzer**: Telemetry-driven analysis evaluating session tokens and compaction rates to trigger Recursive Language Model training.
  - **CLI Memory Commands**: Added `vibe memory status` and `vibe wiki expire` for memory system management.
- **Phase 2 Skill System**: Native vibe skill format (TOML + Markdown), atomic installation from git/tarball/local, and step-by-step verification.
- **Embedding Unification**: Shared `vibe/harness/embeddings.py` module with `fastText` singleton loader and LRU cache (1000 entries).
- **Secret Redaction**: `SecretRedactor` with 9 default patterns (OpenAI, AWS, GitHub, Bearer, etc.) wired into all `TraceStore` backends.
- **CLI Improvements**: `readline` support with persistent history at `~/.vibe/history` and real-time token metrics display.
- **UUID Session Tracking**: Reliable session identification across turns and restarts.

### Changed
- **Memory Optimization**: Switched from `pickle` to `numpy` float32 serialization for embeddings (4x smaller, faster).
- **TraceStore Hardening**: `QueryLoop` now automatically logs sessions on completion via `finally` block.
- **Vector Search Performance**: Added keyword pre-filtering to reduce the search space before expensive vector similarity checks.
- **Persistence**: Implemented atomic writes for `JSONTraceStore` using temp-file + rename pattern.

### Deprecated
- **ConversationStateMachine**: Marked for removal in v2.0; use `QueryLoop` directly for state transitions.

---

## [0.3.1-alpha] — 2026-05-02

### Added
- **Factory-per-Case EvalRunner**: Fresh `QueryLoop.copy()` per eval case with concurrent `asyncio.gather` execution. Eliminates state bleed between runs.
- **Structured FeedbackEngine**: `FeedbackStatus` enum (`OK`, `BELOW_THRESHOLD`, `ENGINE_ERROR`, `VALIDATION_ERROR`) replaces silent 0.5-score footgun with explicit failure mode tracking.
- **Safe SkillExecutor**: `string.Template` is now the primary substitution mechanism (safer than regex). Type coercion for `int/float/bool`, default values via `${VAR:-default}`, and `KeyError` safety on missing variables.
- **Real LLM Summarization Metrics**: `CompactionResult` now tracks `tokens_before`, `tokens_after`, and `summarization_latency_ms`. Telemetry records token savings on successful LLM summarization.
- **5-Layer Security Defense**: `SecurityCoordinator` orchestrates pattern scanning, file safety, human approval gates, smart approver, and checkpoints+rollback. Wired into `QueryLoop` before tool execution.
- **Wiki Compiler**: `vibe wiki compile` scans recent traces, extracts knowledge, and creates draft pages in `pending/` for human review. `vibe wiki review` supports approve/reject workflow.

### Changed
- **EvalRunner.run_all()**: Now creates fresh QueryLoop copies per case and runs them concurrently under the existing semaphore.
- **FeedbackCoordinator.evaluate()**: Uses `FeedbackStatus` for retry decisions — skips retries on `ENGINE_ERROR` and `VALIDATION_ERROR`.
- **ContextCompactor**: Efficiency metrics tracked on all compaction paths. Telemetry hook receives `tokens_before` for accurate reporting.

---

## [0.2.0-alpha] — 2026-04-19

### Added
- **Multi-Provider Support**: Introduced `ProviderRegistry` and `ModelRegistry` for managing multiple LLM endpoints (OpenRouter, Anthropic, Ollama, etc.).
- **Provider Adapters**: Implemented `OpenAIAdapter` and `AnthropicAdapter` to support diverse API formats.
- **Cross-Provider Fallback**: `LLMClient` now dynamically resolves connection details, enabling fallback chains to span different providers and adapters.
- **Circuit Breaker**: Integrated resilience into `LLMClient` to automatically skip unstable model endpoints during cooldown periods.
- **Custom Headers**: Added `extra_headers` support at the provider level, enabling "Roo Code" simulation for OpenRouter and support for beta API features.
- **Comprehensive Documentation**: Added `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/ROADMAP.md`, `docs/EVALUATION.md`, and `docs/REVIEWS.md`.

### Fixed
- **Security**: Hardened `BashTool` by switching from `subprocess_shell` to `subprocess_exec` and implemented strict path jailing in `FileTool` and `SkillManageTool`.
- **Stability**: Fixed resource leaks by ensuring `httpx.AsyncClient` is properly closed across all runners and coordinators.
- **Query Loop Integrity**: Resolved ambiguous `COMPLETED` states by adding an explicit `INCOMPLETE` state for iteration exhaustion.

### Changed
- **Architecture**: Decomposed the monolithic `QueryLoop` into specialized coordinators: `ToolExecutor`, `FeedbackCoordinator`, and `CompactionCoordinator`.
- **Refactoring**: Standardized configuration parsing in `VibeConfig` and unified typing styles across the core package.
- **Project Cleanup**: Consolidated planning and review documents and rewrote `README.md` for better project accessibility.

---

## [0.1.0-alpha] — 2026-04-15

### Added
- Initial project scaffold with `pyproject.toml`, `pytest`, and modern Python 3.11+ stack.
- `vibe/core/model_gateway.py` — OpenAI-compatible LLM client with retry, error typing, and structured output coercion.
- `vibe/core/error_recovery.py` — Exponential backoff with jitter and configurable retry policies.
- `vibe/core/query_loop.py` — Main conversation loop with tool-call handling, metrics tracking, and context compaction.
- `vibe/core/context_compactor.py` — Token-aware context management with summarize-middle strategy.
- `vibe/tools/tool_system.py` — Tool registry with OpenAI-style schema generation.
- `vibe/tools/bash.py` — Bash execution tool with sandbox configuration.
- `vibe/tools/file.py` — File read/write tools with pagination.
- `vibe/harness/memory/trace_store.py` — SQLite session and message logging.
- `vibe/harness/memory/eval_store.py` — YAML eval loader and result tracking.
- `vibe/harness/orchestration/sync_delegate.py` — Parallel subagent runner (up to 3 workers).
- `vibe/cli/main.py` — Typer-based CLI for interactive and single-query modes.
- 3 built-in evals: `file_read_001`, `bash_math_001`, `multi_step_001`.
- Project tracking docs: `ROADMAP.md`, `TODO.md`, `CHANGELOG.md`.

### Security
- **Removed hardcoded API key fallback** in `vibe/core/model_gateway.py` and `vibe/cli/main.py`.
- **Hardened BashTool** with regex-based dangerous-pattern denylist (catches `curl | bash` variants, `sudo`, `eval`, fork bombs, etc.) and optional `allowed_commands` whitelist mode.

### Architecture
- Added `vibe/harness/constraints.py` with `HookPipeline` supporting stages:
  - `PRE_VALIDATE` → `PRE_MODIFY` → `PRE_ALLOW` → `POST_EXECUTE` → `POST_FIX`
- Integrated constraint hooks into `QueryLoop` for pre/post tool execution governance.
- Added `QueryState` enum (`IDLE`, `PLANNING`, `PROCESSING`, `TOOL_EXECUTION`, `SYNTHESIZING`, `COMPLETED`, `STOPPED`, `ERROR`) to track loop lifecycle explicitly.

---

*Format loosely based on [Keep a Changelog](https://keepachangelog.com/).*
