# Changelog

All notable changes to Vibe Agent will be documented in this file.

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
