# vibe-agent Improvement Plan

**Derived from:** `CODE_REVIEW_KIMI.md` (Kimi CLI comprehensive review, 2026-04-18)  
**Total items:** 24 recommendations grouped into 5 phases  
**Guiding principle:** Security first, stability second, architecture third, quality fourth, polish last.

---

## Phase 1: Security Fixes (Critical) 🔴

**Goal:** Eliminate all exploitable vulnerabilities before any other work.
**Estimated effort:** ~4 hours  
**Rationale:** These are security vulnerabilities that could allow arbitrary code execution or path traversal. They must be fixed immediately.

### 1.1 Fix BashTool shell injection (`vibe/tools/bash.py`)
- Replace `asyncio.create_subprocess_shell` with `create_subprocess_exec` + `shlex.split()`
- Fix prefix-only whitelist to validate the exact first token
- Harden regex denylist or replace with token-based validation
- Add `os.killpg()` for timeout cleanup (orphaned child processes)
- Fix stdout decoding (`decode(errors='replace')` for binary output)
- **Tests:** Add `test_bash_001_shell_injection_blocked`, `test_bash_002_whitelist_exact_match`, `test_bash_003_timeout_kills_children`

### 1.2 Fix MCPServerConfig mutable defaults (`vibe/tools/mcp_bridge.py`)
- Change `args: List[str] = None` to `args: List[str] = field(default_factory=list)`
- Same for `tools` field
- **Tests:** Add `test_mcp_config_001_mutable_defaults_isolated`

### 1.3 Fix SkillManageTool path traversal (`vibe/tools/skill_manage.py`)
- Fix `startswith` check: append `os.sep` to `base` or use `Path.relative_to()`
- Change `~/.hermes/skills` to `~/.vibe/skills`
- **Tests:** Add `test_skill_manage_001_path_traversal_blocked`

### 1.4 Fix symlink escape in file tools (`vibe/tools/file.py`)
- Do not call `.resolve()` before jail check; use `.absolute()` or resolve-with-check
- **Tests:** Add `test_file_001_symlink_escape_blocked`

**Phase 1 exit criteria:**
- All 4 fixes committed
- New security tests pass
- No regression in existing 159 tests

---

## Phase 2: Core Stability (High) 🟠

**Goal:** Fix resource leaks, ambiguous states, and data integrity issues.
**Estimated effort:** ~6 hours  
**Rationale:** These affect reliability in production eval runs and long-lived sessions.

### 2.1 Add circuit breaker to LLMClient (`vibe/core/model_gateway.py`)
- Track consecutive failures per model
- After N failures (default 5), skip model for cooldown period (default 60s)
- Reset counter on success
- **Tests:** Add `test_circuit_breaker_001_opens_after_failures`, `test_circuit_breaker_002_closes_after_success`, `test_circuit_breaker_003_fallback_works_when_open`

### 2.2 Fix httpx.AsyncClient lifecycle (`vibe/core/model_gateway.py` + consumers)
- Add `async def close()` to `QueryLoop` that calls `self.llm.close()`
- Call it in `EvalRunner`, `SoakTestRunner`, `MultiModelRunner`, `SyncDelegate`
- Add `async with` support or explicit cleanup in factory
- **Tests:** Add `test_resource_001_client_closed_after_run`, `test_resource_002_client_closed_on_exception`

### 2.3 Fix QueryLoop ambiguous COMPLETED state (`vibe/core/query_loop.py`)
- Add `INCOMPLETE` state for `max_iterations` exhaustion
- Set `INCOMPLETE` instead of `COMPLETED` when max iterations hit
- Update `EvalRunner` to distinguish `COMPLETED` vs `INCOMPLETE`
- **Tests:** Update `test_query_loop_001_max_iterations_incomplete`, update `test_eval_runner_001_incomplete_flag`

### 2.4 Fix EvalStore schema mismatch (`vibe/harness/memory/eval_store.py`)
- Add `total_tokens` and `latency_seconds` columns to `_init_db()`
- Provide migration path for existing databases (recreate or ALTER TABLE)
- **Tests:** Add `test_eval_store_001_schema_has_all_columns`

### 2.5 Fix Observability double-default bug (`vibe/evals/observability.py`)
- Ensure `Observability.get_default()` and module-level `obs` are the same instance
- Or remove module-level singleton entirely
- **Tests:** Add `test_observability_001_singleton_identity`

**Phase 2 exit criteria:**
- All stability fixes committed
- New tests pass
- Soak test runs without resource leaks (can validate with `tracemalloc`)

---

## Phase 3: Architecture & Refactoring (High/Medium) 🟡

**Goal:** Reduce God Class, improve naming, and decouple provider-specific code.
**Estimated effort:** ~8 hours  
**Rationale:** These improve long-term maintainability and make the codebase approachable for contributors.

### 3.1 Refactor QueryLoop.run() decomposition (`vibe/core/query_loop.py`)
- Extract `_execute_tools()` from `run()`
- Extract `_apply_feedback()` from `run()`
- Extract `_check_should_continue()` for iteration/feedback logic
- Keep `run()` as thin orchestrator (< 40 lines)
- **Tests:** Ensure existing tests still pass; add `test_query_loop_002_extracted_methods_callable`

### 3.2 Implement real LLM summarization + make configurable (`vibe/core/context_compactor.py`)
- Replace `summarize_middle` with actual LLM-based summarization using `LLMClient`
- Keep `elide_middle` as a lightweight fallback strategy
- Add `keep_n_messages` and `max_chars` as constructor parameters
- Use actual token estimation (not character count) when tiktoken available
- Add timeout/cost guardrails for LLM summarization (fallback to elide if LLM fails)
- **Tests:** Add `test_compactor_001_llm_summary_reduces_tokens`, `test_compactor_002_elide_fallback_on_llm_error`

### 3.3 Narrow RetryPolicy defaults (`vibe/core/error_recovery.py`)
- Change default `retryable_exceptions=(Exception,)` to `(httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException, ConnectionError)`
- Allow override via constructor for flexibility
- **Tests:** Add `test_retry_001_syntax_error_not_retried`, `test_retry_002_network_error_retried`

### 3.4 Refactor EvalRunner assertions (`vibe/evals/runner.py`)
- Replace closure-based `nonlocal` pattern with named functions returning `(bool, str)`
- Extract each assertion type into `_check_tool_sequence()`, `_check_metrics_threshold()`, etc.
- **Tests:** All existing assertion tests should pass without modification

### 3.5 Add connection pooling to MCPBridge (`vibe/tools/mcp_bridge.py`)
- Reuse single `httpx.AsyncClient` across HTTP MCP invocations
- Reuse stdio subprocess across stdio MCP invocations (long-lived sessions)
- Close clients on cleanup
- **Tests:** Add `test_mcp_001_http_client_reused`, `test_mcp_002_stdio_process_reused`

### 3.6 Extract duplicated tool-call name extraction
- Create `vibe/tools/_utils.py` with `_extract_tool_call_name(call)`
- Replace copy-pasted code in `runner.py` and `query_loop.py`
- **Tests:** Add `test_tool_utils_001_extract_name_dict`, `test_tool_utils_002_extract_name_object`

**Phase 3 exit criteria:**
- QueryLoop.run() < 40 lines
- All existing 159+ tests pass
- No functional regressions

---

## Phase 4: Testing & Quality (Medium) 🟡

**Goal:** Fix test anti-patterns, remove dead code, improve coverage.
**Estimated effort:** ~5 hours  
**Rationale:** Tests are the safety net; fixing them now ensures future phases are safe.

### 4.1 Remove asyncio.run() anti-pattern
- Convert `tests/test_eval_runner.py` to use `@pytest.mark.asyncio`
- Check for `asyncio.run()` in other test files
- **Scope:** All affected test files

### 4.2 Remove dead code in tests
- Fix double assignments in `test_query_loop.py`, `test_model_gateway.py`, `test_eval_runner_assertions.py`
- Delete unused imports and variables
- **Tool:** Run `vulture` or manual audit

### 4.3 Add CLI tests (`vibe/cli/main.py`)
- Use `click.testing.CliRunner` (or Typer equivalent) to test commands
- Test: help text, config load, version
- **Tests:** Create `tests/test_cli.py`

### 4.4 Remove FastAPI/uvicorn from core dependencies
- Move `fastapi>=0.110.0` and `uvicorn>=0.29.0` from `[project.dependencies]` to `[project.optional-dependencies]` under `api = ["fastapi", "uvicorn"]`
- Remove empty `vibe/api/` package or add a README explaining it's optional
- No API tests needed (dependencies removed from core)
- **Tests:** Verify `pip install -e ".[api]"` works as optional extra

### 4.5 Add resource leak tests
- Assert `LLMClient.close()` is called after `QueryLoop` usage
- Use `pytest` fixtures with cleanup verification

### 4.6 Fix unsafe mocking patterns
- Replace direct module mutation (`mcp_bridge_module.httpx = FakeHttpx`) with `unittest.mock.patch`
- Ensure class-level patches are cleaned up in `tearDown` or fixtures

**Phase 4 exit criteria:**
- All tests use `@pytest.mark.asyncio` where applicable
- No dead code in tests
- CLI or API coverage gap closed (or dependencies removed)

---

## Phase 5: Decoupling & Polish (Low) 🟢

**Goal:** Remove vendor lock-in, improve configurability, and clean up.
**Estimated effort:** ~4 hours  
**Rationale:** These are nice-to-haves that improve the project's professionalism.

### 5.1 Remove hardcoded Applesay defaults
- Remove `base_url="http://ai-api.applesay.cn"` from `config.py`, `model_gateway.py`, `health_check.py`
- Remove `model="qwen3.5-plus"` hardcoded default
- Remove `"无可用渠道"` checks (use HTTP status codes instead)
- Keep a neutral default (e.g., `http://localhost:11434` for Ollama or require env var)
- **Tests:** Add `test_config_001_requires_explicit_url`, `test_model_gateway_002_provider_agnostic`

### 5.2 Configurability improvements
- Move magic numbers to `VibeConfig`:
  - `context_compactor.py`: `max_chars`, `keep_n_messages`
  - `query_loop.py`: `feedback_threshold`, `max_feedback_retries`
  - `query_loop_factory.py`: `timeout`, `max_iterations`
- Add validation (pydantic or dataclass constraints)

### 5.3 Replace deprecated `datetime.utcnow()`
- Global find/replace with `datetime.now(timezone.utc)`

### 5.4 Add missing .gitignore entries
- `.env`, `.venv/`, `dist/`, `build/`, `.pytest_cache/`

### 5.5 Add `__all__` to package `__init__.py` files
- `vibe/core/__init__.py`, `vibe/tools/__init__.py`, `vibe/harness/__init__.py`

### 5.6 Add request/response logging hooks to LLMClient
- Optional `on_request` / `on_response` callbacks for debugging

**Phase 5 exit criteria:**
- No hardcoded vendor URLs in source
- All magic numbers configurable
- Clean git status

---

## Implementation Order Summary

| Phase | Theme | Items | Est. Hours |
|-------|-------|-------|------------|
| 1 | Security | 4 | 4 |
| 2 | Stability | 5 | 6 |
| 3 | Architecture | 6 | 8 |
| 4 | Test Quality | 6 | 5 |
| 5 | Decoupling | 6 | 4 |
| **Total** | | **27 tasks** | **~27 hours** |

## Decision Points for User

| # | Decision | User Choice |
|---|----------|-------------|
| 1 | FastAPI/uvicorn | **B.** 从 core deps 移除，保持代码简洁 |
| 2 | `summarize_middle` | **B.** 实现真正的 LLM 总结，`elide_middle` 作为 fallback |
| 3 | 默认 LLM 端点 | **A.** 保留中性默认（如 `http://localhost:11434` for Ollama） |

*Decisions recorded: 2026-04-18*

## Review Gates

Per user's preferred workflow:
- After EACH phase: manual code review or Gemini CLI review
- User approval required before proceeding to next phase
- All 159 existing tests must pass before phase completion

---

*Plan created: 2026-04-18*  
*Ready for user review and approval*
