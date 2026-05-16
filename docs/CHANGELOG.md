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

## [0.3.3-alpha] — 2026-05-15

### Added — Weakness Mitigations (19 total gaps closed)
- **Adaptive Iteration Budgets** (`vibe/core/adaptive_budget.py`): Complexity-based depth allocation with stagnation/completion/token signals. Replaces hard `max_iterations=50`.
- **Latency-Aware Routing** (`vibe/core/latency_tracker.py`): Rolling-window p50/p95 stats with error-rate filtering for model selection.
- **Cost Tracking** (`vibe/core/cost_tracker.py`): Per-session + daily + global spend limits with `BudgetExceededError`.
- **Session Recovery** (`vibe/core/session_recovery.py`): TTL-based checkpoint/restore for crash recovery.
- **Adversarial Evals** (`vibe/evals/adversarial.py`): Pattern-based prompt injection, jailbreak, and exfiltration detection.
- **Eval Dashboard** (`vibe/evals/dashboard.py`): Dark-themed HTML report generator with pass-rate bars and run history.
- **RLM Training Pipeline** (High severity fix): `RLMThresholdAnalyzer.analyze_and_train()` now launches actual LoRA fine-tuning via background task + subprocess worker. No longer log-only.
- **Semantic Deduplication** (`vibe/memory/semantic_dedup.py`): Vector similarity for `_find_existing_page` with Jaccard fallback.
- **Typed Skill Variables** (`vibe/harness/skills/typed_vars.py`): Type coercion (int/float/bool/str/list/dict), default values, schema validation.
- **Skill Orchestrator** (`vibe/harness/skills/orchestrator.py`): Skills can `await` other skills and spawn sub-agents via `asyncio.gather`.
- **Skill Marketplace** (`vibe/harness/skills/marketplace.py`): JSON registry with search, install, and rating support.
- **Dynamic Tool Declaration** (`vibe/harness/skills/dynamic_tools.py`): Skills declare tools at runtime via `DynamicToolRegistry`.
- **Vector Index Upgrade** (`vibe/memory/vector_index_upgrade.py`): Transparent migration from fastText to sentence-transformers with KeywordIndex fallback.
- **Wiki Graph Database** (`vibe/memory/wiki_graph.py`): Entity nodes, relationship edges, entity resolution via alias merging.
- **Per-Tag Novelty Thresholds** (`vibe/memory/novelty_thresholds.py`): Domain-specific dedup strictness (e.g., finance=0.8, general=0.3).
- **TelemetryCollector** (`vibe/memory/telemetry_collector.py`): Decouples `memory_status` CLI from `wiki.db.conn` direct access.
- **Shared Circuit Breaker** (`vibe/core/shared_circuit_breaker.py`): FlashLLMClient uses same `CircuitBreaker` as main `LLMClient`.
- **Factory-per-Case EvalRunner** (`vibe/evals/factory_runner.py`): Fresh `QueryLoop` per eval case eliminates state bleed.

### Architecture
- All new features default-disabled with opt-in via constructor flags.
- 23 new modules, 22 new test files, 1245+ tests passing.

---

## [0.3.2-alpha] — 2026-05-13

### Added
- **Preference Layer (Phase 3.7)**: 8 persistent, testable, code-based heuristics converting user feedback into agent behavior:
  - **Tool Preferences** (`ToolPreferenceRegistry`): Default argument overrides per tool with glob pattern matching (e.g., "always `git diff --stat`").
  - **Approval Rules** (`ApprovalPolicyDB`): Learned auto-approve/deny from user decisions. Deny-before-allow evaluation for security. Path traversal protection via `Path.resolve()` + dual-match logic.
  - **Response Style Policy** (`ResponseStylePolicy`): User-tuned system prompt injection (verbosity, plan format, confirm threshold, show commands).
  - **Macro Sessions** (`MacroSessionRunner`): User-defined multi-step YAML workflows with Jinja2 templating and `SandboxedEnvironment` for SSTI protection.
  - **Recovery Rules** (`RecoveryRuleDB`): Pattern-based error recovery with per-session attempt limits (tracked in session state, not persisted).
  - **Compaction Policy** (`CompactionPolicy`): User-tuned context window management (max tokens, preserve recent N, compression ratio, per-tool priority).
  - **Provider Preference Matrix** (`ProviderPreferenceMatrix`): Per-task model routing learned from user overrides with confidence scoring and fallback chains.
  - **Extraction Policy** (`ExtractionPolicy`): Wiki knowledge filtering (skip patterns, auto-tags, merge threshold). Case-insensitive matching.
- **Preference Registry** (`PreferenceRegistry`): SQLite WAL-backed persistence across 7 domains. Batch hit counting (in-memory, flushed on session end). INFERRED-only stale rule pruning (EXPLICIT rules protected).
- **56 new preference tests** across 11 test files (`tests/preferences/`).

### Changed
- **Session Store**: Improved WAL mode reliability using `closing()` + explicit transaction context.
- **Git Shadow**: Stash state tracking with automatic rollback on restore failure.
- **Test Isolation**: Removed `os.chdir()` anti-pattern from shadow branch tests; use `cwd=repo` instead.

### Wired
- **P1-P8 Main Loop Integration**: All 8 preference types now wired into production code paths:
  - ToolPreferences → `ToolExecutor` (default arg merging)
  - ApprovalRules → `SecurityCoordinator` (deny-before-allow gate)
  - StylePolicy → `QueryLoop.run()` (system prompt injection)
  - MacroSessions → `vibe macro run` CLI (QueryLoopFactory injection for real tool execution)
  - RecoveryRules → `QueryLoop` error handler (`_try_recovery` with session attempt tracking)
  - CompactionPolicy → `ContextCompactor` constructor (overrides max_tokens/preserve_recent)
  - ProviderMatrix → `CostRouter.route()` (user preference override before cost routing)
  - ExtractionPolicy → `KnowledgeExtractor` (skip patterns + auto-tags)
- Config-gated initialization via `QueryLoopFactory` with per-type `*_enabled` flags.

### Architecture
- New `vibe/preferences/` package with shared `PreferenceRule`/`PreferencePolicy` Pydantic models.
- All preference features default-disabled with opt-in via config.
- Plan: `docs/plans/2026-05-09-preference-layer.md`

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
