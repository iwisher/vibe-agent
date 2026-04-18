# Comprehensive Code Review: vibe-agent

**Review Date:** 2026-04-18  
**Reviewer:** Kimi CLI (kimi-code/kimi-for-coding)  
**Scope:** `vibe/` source tree, `tests/` suite, `pyproject.toml`, CLI/API entry points  
**Focus:** QueryLoop state machine, Model Gateway, Planner, Context Compactor, Eval Runner, Test Patterns

---

## 1. ARCHITECTURE & DESIGN

### Overall Structure
The codebase follows a **layered architecture** with reasonable package boundaries:
- `vibe/core/` — State machine, LLM gateway, compaction, recovery
- `vibe/harness/` — Planning, constraints/hooks, feedback, instructions, memory
- `vibe/evals/` — Runner, soak tests, multi-model benchmarking, observability
- `vibe/tools/` — Tool system, bash/file/MCP bridges

However, **separation of concerns is weak at the center**. The `QueryLoop` (358 lines, 13 constructor parameters) is a **God Class** that orchestrates planning, LLM calls, tool execution, hook pipelines, feedback self-verification, MCP bridging, metrics calculation, and context compaction. This violates SRP and makes the class difficult to test in isolation.

### State Machine Design (`vibe/core/query_loop.py`)
The `QueryState` enum defines 8 states (`IDLE` through `ERROR`), but the state machine is **informal and under-utilized**:
- States are set via `_set_state()` but there is **no transition validation** — any state can transition to any other state.
- No state-entry/exit hooks. The `STOPPED` and `ERROR` states are set but consumers must manually inspect `result.state` to detect them.
- `COMPLETED` is ambiguous: it is set both after a successful final response *and* after `max_iterations` exhaustion (lines 275–276), with no signal to the caller that the answer may be incomplete.

### Plugin/Harness Architecture
The **hook pipeline** (`vibe/harness/constraints.py`) is the strongest architectural element. It implements a 5-stage chain (`PRE_VALIDATE` → `PRE_MODIFY` → `PRE_ALLOW` → `POST_EXECUTE` → `POST_FIX`) with clean `HookContext`/`HookOutcome` dataclasses. This is extensible and well-structured.

Conversely, the **planner** (`vibe/harness/planner.py`) is the weakest link. It performs naive substring matching with arbitrary weighting (name ×2, description ×1) and falls back to returning **all tools** when none match (`return matched if matched else tools`). This is a context-window bomb waiting to happen. The `PlanRequest.history_summary` field is accepted but never used.

### Model Gateway Abstraction (`vibe/core/model_gateway.py`)
The `LLMClient` abstracts HTTP calls and fallback chains, but **vendor lock-in is baked into the defaults**:
- Hardcoded `base_url="http://ai-api.applesay.cn"` (line 48)
- Hardcoded `model="qwen3.5-plus"` (line 49)
- Chinese error string `"无可用渠道"` (no available channel) is checked at line 108, coupling generic infrastructure to a specific provider's error messages

The fallback chain logic is sound — iterating `[primary] + fallback_chain` and stopping on first success — but there is **no circuit breaker**. A degraded endpoint will be hammered on every eval case.

### Context Compaction Strategy (`vibe/core/context_compactor.py`)
The compactor supports two strategies (`truncate` and `summarize_middle`), but the naming is misleading: `summarize_middle` does **not** use an LLM to summarize. It replaces middle messages with a static placeholder: `[Context summarized: X earlier messages omitted]`. The strategy is essentially a hard truncation with a euphemistic name.

Key design flaws:
- Hardcoded `keep last 4 messages` heuristic (line 63) with no configurability
- `_truncate()` operates on characters (`max_chars=4000`) not tokens, making it ignorant of actual context window consumption
- No handling of `tool_calls` token count during truncation — a message with large tool call payloads can still exceed limits after "compaction"

### Eval Framework (`vibe/evals/runner.py`)
The `EvalRunner` is well-conceived: it consumes the `QueryLoop` async generator, accumulates results, and runs assertion checks against expectations (file existence, stdout content, tool call sequence, metrics thresholds). The semaphore-based concurrency (`max_concurrency=3`) is appropriate.

However, the assertion implementation uses a **fragile `nonlocal` mutation pattern** (lines 100+). Closure-based checks mutate `passed` and `diff` from nested functions, making the control flow difficult to follow and prone to subtle bugs.

---

## 2. CODE QUALITY

### Code Smells & Duplication

**Duplicated tool-call name extraction** appears in both `vibe/evals/runner.py` (lines 162–167 and 190–195) and `vibe/core/query_loop.py` (lines 216–219). The same `isinstance(call, dict)` branching with `getattr` fallback is copy-pasted. This should be a shared utility like `_extract_tool_call_name(call)`.

**Duplicated path redirection logic** exists in `vibe/tools/bash.py` and `vibe/tools/file.py` — both independently rewrite `/tmp/vibe_*` paths to `$VIBE_EVAL_WORK_DIR`. The implementations differ slightly, creating maintenance risk.

**Duplicated Chinese unavailability string** `"无可用渠道"` appears in `vibe/core/model_gateway.py` and `vibe/core/health_check.py`.

### Overly Complex Functions

| Function | File | Lines | Issue |
|----------|------|-------|-------|
| `QueryLoop.run()` | `query_loop.py` | ~90 | Async generator with nested try/finally, state transitions, planner injection, feedback loop, and tool execution all in one method |
| `EvalRunner.run_case()` | `runner.py` | ~120 | Closure-heavy assertion logic with `nonlocal` mutations |
| `SoakTestRunner.run()` | `soak_test.py` | ~80 | Signal handling, checkpointing, progress reporting, and throttling interleaved |
| `MultiModelRunner.run_all()` | `multi_model_runner.py` | ~60 | Parallel model execution with nested result aggregation |

### Naming Issues

- **`summarize_middle`** (`context_compactor.py`) — does not summarize. Should be `elide_middle` or `compress_middle`.
- **`SyncDelegate`** (`vibe/harness/orchestration/sync_delegate.py`) — is entirely async. Misleading name.
- **`APPLEsay_BASE`** (`vibe/evals/model_registry.py`) — inconsistent capitalization.
- **`_score_text`** (`planner.py`) — returns an `int`, not a normalized score. `score` implies floating point.

### Inconsistent Patterns

- **Type hints coverage is spotty**. `trace_store: Optional[Any]` in `QueryLoop.__init__` defeats the purpose of typing. `tool_calls: Optional[list]` lacks element type annotations.
- **Mixed dataclass styles**: `Metrics` uses plain defaults; `LLMResponse.usage` uses `default_factory=lambda: {...}`; some dataclasses are frozen, others are not.
- **Import style inconsistency**: `json` is imported locally inside `_execute_tool_calls` (line 279, `query_loop.py`) but at module top elsewhere. `asyncio` is imported locally inside `resolve_with_retry` (line 141, `health_check.py`).

---

## 3. ERROR HANDLING

### Robustness & Edge Cases

**Retry policy is dangerously broad.** `RetryPolicy` defaults to `retryable_exceptions=(Exception,)`, meaning it will retry `SyntaxError`, `TypeError`, `ValueError`, `AttributeError`, and virtually everything else. Deterministic bugs will be retried pointlessly, burning quota and delaying failure reports. The default should be `(httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException, ConnectionError)`.

**Feedback failure is silently neutralized.** `FeedbackEngine._run_feedback_prompt()` catches all exceptions and returns `score=0.5` with `issues=["Feedback evaluation failed."]`. A 0.5 score reads as "acceptable," masking real system degradation. Failures should return `score=0.0` or raise an observability event.

**Max iterations exhaustion is silent.** When `QueryLoop` hits `max_iterations`, it sets state to `COMPLETED` with no error flag, no warning, and no `QueryResult` field indicating truncation. Callers (including `EvalRunner`) cannot distinguish between a clean finish and an aborted loop.

### Retry Logic & Recovery

**No circuit breaker** anywhere in the stack. `LLMClient`, `ErrorRecovery`, and `ModelHealthChecker` all retry without tracking failure rates. In a batch eval run or soak test, a downed model will be probed hundreds of times.

**LLM client connection leaks.** `LLMClient` creates an `httpx.AsyncClient` in `__init__` and provides a `close()` method, but:
- `QueryLoop` never calls `llm.close()`
- `QueryLoopFactory` never exposes cleanup
- `EvalRunner` does not close the loop's client after cases
- `SoakTestRunner` does close it, but only in a `finally` block that may not run on `SystemExit`

**Resource leak on generator abandonment.** If a caller stops iterating over `QueryLoop.run()` (e.g., due to a timeout), the `finally` block that resets `self._running` may not execute, leaving the loop in an unrestartable state.

### Failure Mode Gaps

- **Tool execution exceptions** in `ToolSystem.execute_tool()` are caught and returned as `ToolResult.error`, but **stack traces are lost** (line 61–64). Debugging tool failures is extremely difficult.
- **MCPBridge stdio mode** spawns a new subprocess for every tool call. If the subprocess hangs, there is no timeout on the stdio communication — only the outer `asyncio.wait_for` in `SyncDelegate` would catch it.
- **YAML parsing in `InstructionLoader._parse_frontmatter()`** swallows all exceptions with `except Exception: pass`, returning empty metadata for malformed skills.
- **`EvalStore.load_builtin_evals()`** silently drops `total_tokens` and `latency_seconds` fields because `_init_db()` does not create columns for them.

---

## 4. TESTING

### Coverage Overview

The test suite has **22 test files** covering most major modules, but coverage is uneven:

| Module | Test File | Quality |
|--------|-----------|---------|
| `query_loop.py` | `test_query_loop.py`, `test_query_loop_edge.py` | Good state machine coverage; missing concurrency/stop races |
| `model_gateway.py` | `test_model_gateway.py`, `test_fallback.py` | Good error classification; missing retry timing, network errors |
| `context_compactor.py` | `test_context_compactor.py` | Adequate; no tiktoken integration test |
| `eval_runner.py` | `test_eval_runner.py`, `test_eval_runner_assertions.py` | Assertions covered; poor async patterns |
| `planner.py` | `test_planner.py` | Shallow — only tests keyword matching, not edge cases |
| `error_recovery.py` | `test_error_recovery.py` | **Minimal** — 68 lines, no backoff timing tests |
| `mcp_bridge.py` | `test_mcp_bridge.py`, `test_mcps.py` | Good paths; missing HTTP error/timeout tests |
| `trace_store.py` | `test_trace_store.py` | Vector tests may be non-deterministic |
| `sync_delegate.py` | `test_sync_delegate.py` | **Very thin** — 66 lines, no error handling tests |

### Critical Coverage Gaps

1. **No CLI tests.** `vibe/cli/main.py` has zero test coverage.
2. **No API tests.** Despite FastAPI/uvicorn dependencies, `vibe/api/` is entirely untested.
3. **No integration tests with real LLMs.** Understandable, but there are no recorded fixture-based integration tests either.
4. **No concurrency/race condition tests.** The `QueryLoop` `_running` flag, semaphore usage in `EvalRunner`, and parallel model execution in `MultiModelRunner` are all untested for races.
5. **No resource leak tests.** `LLMClient.close()` is never asserted to be called.

### Test Quality Issues

**Anti-pattern: `asyncio.run()` in sync tests.** `tests/test_eval_runner.py` wraps every async test in `asyncio.run()` instead of using `@pytest.mark.asyncio`. This bypasses pytest-asyncio's event loop management and fixture lifecycle.

**Anti-pattern: Dead code in tests.** Multiple files have double assignments where the first is immediately overwritten:
- `tests/test_query_loop.py` lines 66–73
- `tests/test_model_gateway.py` lines 65–77
- `tests/test_eval_runner_assertions.py` lines 118–130

**Anti-pattern: Script copying for CLI tests.** `tests/test_validate_eval_tags.py` copies `scripts/validate_eval_tags.py` to a temp dir and performs text substitution to patch hardcoded paths. This is extremely brittle — any string format change in the script breaks all tests.

**Anti-pattern: Heavy private attribute access.** `tests/test_observability.py` extensively accesses `obs._spans`, `obs._gauges`, `obs._histograms`, making tests brittle to internal refactoring.

### Mocking Strategies

Mocking is generally reasonable (AsyncMock for LLM, httpx.Response patching, tmp_path for filesystem), but there are unsafe patterns:
- `tests/test_mcp_bridge.py` directly mutates `mcp_bridge_module.httpx = FakeHttpx()` instead of using `unittest.mock.patch`, risking cross-test pollution.
- `tests/test_sync_delegate.py` patches `QueryLoop.run` at the class level without guaranteed cleanup.

---

## 5. SECURITY

### Tool Execution

**`BashTool` uses `asyncio.create_subprocess_shell`** (`vibe/tools/bash.py`, line 137). This is inherently dangerous because the entire command string is passed to a shell. Even with a regex denylist, shell injection is possible via encoding tricks, backticks, variable expansion, or chaining (`ls; rm -rf /`).

The **whitelist is prefix-only** (`_check_whitelist` uses `startswith`). If `allowed_commands=["ls"]`, then `ls; rm -rf /` passes.

The **regex denylist is bypassable**. For example, `r"\bsudo\b"` can be circumvented with `sud'o'` or `/usr/bin/sudo` (word boundary `\b` handles some cases but not all encoding tricks).

**Orphaned child processes on timeout.** `proc.kill()` kills only the shell process, not children it spawned. A process group kill (`os.killpg`) is needed.

**`BashTool` stdout decoding uses strict mode.** `stdout_data.decode()` (no error handler) will crash on binary or invalid UTF-8 output.

### MCP Handling

**`MCPServerConfig` has mutable default arguments** (`vibe/tools/mcp_bridge.py`, lines 21–22):
```python
args: List[str] = None
tools: List[Dict[str, Any]] = None
```
Since it's a `@dataclass` without `field(default_factory=list)`, these are **shared mutable defaults across all instances**. If one config modifies `args`, all configs see it.

**The MCP implementation is not actually MCP-compliant.** The JSON payload uses `{"tool": ..., "arguments": ...}` (lines 78–81, 100). The real MCP protocol uses JSON-RPC 2.0. This will not interoperate with standard MCP servers.

**No connection reuse for HTTP MCP.** `_invoke_http` creates a new `httpx.AsyncClient` for every request. No keep-alive, no connection pooling.

**Stdio MCP spawns a new subprocess per tool call.** This is extremely inefficient and breaks stateful MCP servers.

### Config Loading

**`VibeConfig.load()` auto-creates `~/.vibe/config.yaml`** with hardcoded defaults. There is no validation that the written file has secure permissions (e.g., `0o600`). API keys in the config file could be world-readable.

**`pyproject.toml` puts `pytest` and `pytest-asyncio` in core dependencies.** These should be in `[project.optional-dependencies]` under a `test` extra, not installed at runtime.

### Eval YAML Parsing

**`EvalStore.load_builtin_evals()`** uses `yaml.safe_load()` (safe), but tag parsing is fragile:
```python
tag_names = {t.split("=")[0] if "=" in t else t for t in tags}
```
This breaks if `=` appears in the value portion of a tag (e.g., `category=data=science`).

**`InstructionLoader._parse_frontmatter()`** splits on `---`, which could appear inside markdown content (horizontal rules), corrupting parsing.

### File System Security

**`WriteFileTool` has no backup/overwrite protection.** It can silently overwrite any file within the jail.

**`ReadFileTool` reads entire files into memory** with `f.readlines()` even if only 500 lines are requested.

**`SkillManageTool` path traversal guard is buggy** (`vibe/tools/skill_manage.py`, lines 62–64):
```python
if not str(resolved).startswith(str(base)):
```
`base` lacks a trailing path separator. `/home/user/.hermes/skills-evil/foo` starts with the base string and passes the check. (Also: the path references `~/.hermes/skills` instead of `~/.vibe/skills`.)

**Symlink escape in `_resolve_and_jail`** (`vibe/tools/file.py`): `Path.resolve()` follows symlinks. A symlink inside `root_dir` pointing outside will pass the `relative_to` check because `resolve()` returns the final target path.

---

## 6. PERFORMANCE

### Bottlenecks & Inefficient Algorithms

**`TraceStore.get_similar_sessions_vector()`** (`vibe/harness/memory/trace_store.py`) loads **ALL embeddings into memory** and computes cosine similarity in pure Python. This is O(N) memory and O(N) CPU per query. For a trace store with thousands of sessions, this will be a major bottleneck. There is no vector index (no `faiss`, no `sqlite-vss`, no approximate nearest neighbors).

**`MultiModelRunner.run_model()`** creates a **brand new `QueryLoop` for every single eval case** (line 95). This re-initializes HTTP clients, tool systems, and state repeatedly, causing massive overhead in batch evals.

**`ContextPlanner._select_tools()`** falls back to returning **all tools** when no keywords match. If a tool system has 50+ tools, this bloats every LLM prompt with irrelevant schemas.

**`Observability._metrics` grows unbounded.** Every call to `counter()`/`gauge()`/`histogram()` appends to a list. Long soak tests will consume unbounded memory.

### Unnecessary Computations

**`ContextCompactor.estimate_tokens()`** encodes `tool_calls` as `str(tool_calls)` when estimating tokens. This produces wildly inaccurate counts compared to actual API tokenization of tool call structures.

**`EvalRunner.run_case()`** consumes the entire `QueryLoop.run()` generator into a list (`results.append(result)`), then only uses `results[-1]` and a filtered error list. The intermediate results are largely discarded after iteration.

**`MultiModelRunner` tag aggregation** does a linear search `next((c for c in cases if c.id == case_result.eval_id), None)` for every case result. A `dict` lookup would be O(1).

### Memory Concerns

**`pickle` is used for embedding serialization** in `TraceStore`. `pickle` is insecure for untrusted data and is not portable across Python versions. It also produces larger on-disk representations than formats like `numpy` arrays or `msgpack`.

**`TraceStore` lazily loads `all-MiniLM-L6-v2`** per instance. Multiple `TraceStore` instances load multiple copies of the model into memory.

**`QueryLoop` message history is never pruned** except by the compactor. In long-running sessions, `self.messages` grows indefinitely until the compactor triggers.

---

## 7. MAINTAINABILITY

### Documentation Quality

Docstrings exist for most public classes and methods, but they are often **minimal and sometimes misleading**:
- `ContextPlanner` docstring does not mention that it falls back to all tools.
- `summarize_middle` strategy docstring (implied by name) suggests LLM-based summarization, but it is just message elision.
- `SyncDelegate` docstring does not clarify that it is async.

There is **no architecture documentation** in the repo explaining how the QueryLoop, harness, and evals interact. `AGENTS.md` is referenced in the project description but was empty in this working tree.

### Type Hints Coverage

Type hints are present but **inconsistent and frequently weakened with `Any`**:
- `trace_store: Optional[Any]` in `QueryLoop.__init__`
- `tool_calls: Optional[list]` (no element type) in `Message`
- `mcp_bridge: Optional[MCPBridge]` is typed, but `MCPBridge` itself uses loosely typed `Dict[str, Any]` for tool schemas

Several functions lack return type annotations, particularly in `vibe/evals/soak_test.py`.

### Configurability

**Magic numbers are pervasive** and rarely externally configurable:
| Value | Location | Configurable? |
|-------|----------|---------------|
| `max_chars=4000` | `context_compactor.py:80` | No |
| `keep last 4 messages` | `context_compactor.py:63` | No |
| `feedback_threshold=0.7` | `query_loop.py:70` | Only via constructor |
| `max_feedback_retries=1` | `query_loop.py:71` | Only via constructor |
| `timeout=120` | `query_loop_factory.py` (bash) | No |
| `jitter: bool = True` | `error_recovery.py` | Only via `RetryPolicy` |
| `max_iterations=15` | `query_loop_factory.py:from_profile` | No |

The `VibeConfig` system (`vibe/core/config.py`) is well-designed with layered overrides (file → env var), but it only covers LLM, fallback, and eval paths. Operational parameters (compaction thresholds, feedback settings, hook policies) are not in config.

### Extensibility

**`QueryLoopFactory.create_tool_system()`** hardcodes exactly three tools (`BashTool`, `ReadFileTool`, `WriteFileTool`). There is no registry hook or configuration for adding custom tools without bypassing the factory.

**`vibe/core/__init__.py` and `vibe/tools/__init__.py` are empty**, forcing deep imports. This suggests module boundaries are not yet stabilized.

**The API layer is entirely empty.** `pyproject.toml` declares `fastapi>=0.110.0` and `uvicorn>=0.29.0` as core dependencies, but `vibe/api/` contains only empty `__init__.py` files. This is dependency bloat.

---

## 8. SPECIFIC RECOMMENDATIONS (Prioritized)

### 🔴 Critical — Fix Immediately

1. **Fix `BashTool` shell injection vulnerability** (`vibe/tools/bash.py`)
   - Replace `create_subprocess_shell` with `create_subprocess_exec` using `shlex.split()` or a parsed argument list.
   - Fix prefix-only whitelist to validate the entire command (e.g., tokenize with `shlex` and check the first token exactly).
   - Add `os.killpg()` for timeout cleanup.

2. **Fix `MCPServerConfig` mutable default bug** (`vibe/tools/mcp_bridge.py:21–22`)
   ```python
   # Change from:
   args: List[str] = None
   # To:
   args: List[str] = field(default_factory=list)
   ```

3. **Fix `SkillManageTool` path traversal** (`vibe/tools/skill_manage.py:62–64`)
   - Append `os.sep` to `base` before `startswith` check, or use `Path.relative_to()` with exception handling.
   - Change `~/.hermes/skills` to `~/.vibe/skills`.

4. **Fix symlink escape in file tools** (`vibe/tools/file.py`)
   - Do not call `.resolve()` before the jail check. Use `.absolute()` or check the resolved path after following symlinks with explicit bounds.

### 🟠 High — Address Soon

5. **Add circuit breaker to `LLMClient`** (`vibe/core/model_gateway.py`)
   - Track failure counts per model. After N consecutive failures, skip the model for a cooldown period.

6. **Refactor `QueryLoop.run()`** (`vibe/core/query_loop.py`)
   - Extract tool execution into `_execute_tools()`
   - Extract feedback retry into `_apply_feedback()`
   - Add `INCOMPLETE` state for `max_iterations` exhaustion

7. **Close `httpx.AsyncClient` consistently**
   - Add `async def close()` to `QueryLoop` that calls `self.llm.close()`
   - Call it in `EvalRunner`, `SoakTestRunner`, `MultiModelRunner`, and `SyncDelegate`

8. **Replace `summarize_middle` with accurate naming** (`vibe/core/context_compactor.py`)
   - Rename to `elide_middle` or implement actual LLM-based summarization.
   - Make `keep_n_messages` and `max_chars` configurable via constructor.

9. **Fix `EvalStore` schema mismatch** (`vibe/harness/memory/eval_store.py`)
   - Add `total_tokens` and `latency_seconds` columns to `_init_db()`.

10. **Fix `Observability` double-default bug** (`vibe/evals/observability.py`)
    - Ensure `Observability.get_default()` and module-level `obs` are the same instance, or remove the module-level singleton.

### 🟡 Medium — Technical Debt

11. **Narrow `RetryPolicy.retryable_exceptions`** (`vibe/core/error_recovery.py`)
    - Default to `(httpx.HTTPStatusError, httpx.NetworkError, httpx.TimeoutException, ConnectionError)` instead of `(Exception,)`.

12. **Refactor `EvalRunner` assertions** (`vibe/evals/runner.py`)
    - Replace closure-based `nonlocal` pattern with simple functions returning `(bool, str)`.

13. **Add connection pooling to `MCPBridge`** (`vibe/tools/mcp_bridge.py`)
    - Reuse `httpx.AsyncClient` across HTTP MCP invocations.
    - Reuse stdio subprocesses across stdio MCP invocations (long-lived sessions).

14. **Implement actual vector index for `TraceStore`** (`vibe/harness/memory/trace_store.py`)
    - Use `sqlite-vss`, `faiss`, or at minimum batch similarity computation with `numpy`.

15. **Move `pytest` to optional dependencies** (`pyproject.toml`)
    - Create `[project.optional-dependencies]` with `test = ["pytest", "pytest-asyncio", ...]`.

16. **Add `__all__` and exports to package `__init__.py` files**
    - `vibe/core/__init__.py`, `vibe/tools/__init__.py`, `vibe/harness/__init__.py`

17. **Remove or implement API layer**
    - Either implement FastAPI endpoints in `vibe/api/` or remove `fastapi`/`uvicorn` from core dependencies.

### 🟢 Low — Polish

18. **Add request/response logging hooks to `LLMClient`** for observability and debugging.
19. **Replace `datetime.utcnow()`** (deprecated in Python 3.12) with `datetime.now(timezone.utc)` across the codebase.
20. **Add `.env`, `.venv/`, `dist/`, `build/` to `.gitignore`** and untrack `.pytest_cache/` from git.
21. **Remove dead code** from tests (double assignments in `test_query_loop.py`, `test_model_gateway.py`, `test_eval_runner_assertions.py`).
22. **Replace `asyncio.run()` anti-pattern** in `tests/test_eval_runner.py` with `@pytest.mark.asyncio`.
23. **Add `max_tokens=0` validation** in `LLMClient.complete()` — currently `if max_tokens:` silently ignores `0`.
24. **Standardize on provider-agnostic defaults** — remove hardcoded `applesay.cn` URLs from `config.py`, `model_gateway.py`, and `health_check.py`. Use environment-only resolution or require explicit configuration.

---

## Summary

The vibe-agent codebase demonstrates **solid architectural intent** — a state-machine-driven agent loop with pluggable hooks, eval-driven benchmarking, and multi-model fallback. The harness hook pipeline and eval framework are particularly well-conceived.

However, the codebase suffers from **tight vendor coupling** (Applesay-specific URLs and error strings), **a centralized God Class** (`QueryLoop`), **several security vulnerabilities** in tool execution and path handling, **missing resource lifecycle management** (HTTP client leaks), and **misleading naming** (`summarize_middle`, `SyncDelegate`). The test suite covers happy paths reasonably well but has significant gaps in concurrency, error handling, CLI, and API testing.

The most impactful improvements would be: (1) fixing the `BashTool` shell injection and `SkillManageTool` path traversal, (2) refactoring `QueryLoop` into smaller responsibilities, (3) adding consistent resource cleanup, and (4) decoupling from provider-specific defaults.
