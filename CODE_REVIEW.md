# Vibe-Agent Code Review & Critique

**Date:** 2026-04-18
**Scope:** Full codebase review covering `vibe/`, `tests/`, CLI, and eval infrastructure
**Focus:** Architecture, implementation, security, performance, testing

---

## 1. Overall Architecture & Design Patterns

### Strengths
- **Clean layering**: `core/` (LLM loop), `tools/` (pluggable), `harness/` (cross-cutting), `evals/` (hill-climbing) is intuitive.
- **Harness hook pipeline**: `PRE_VALIDATE` → `PRE_MODIFY` → `PRE_ALLOW` → `POST_EXECUTE` → `POST_FIX` is solid for injecting safety without clutter.
- **Eval-driven design**: YAML cases, runner, scorecards, soak tests — the right long-term bet for an agent harness.
- **Async-first**: Correct for I/O-bound agents.

### Weaknesses
- **Tight coupling in QueryLoop**: 12 constructor arguments. God-class anti-pattern.
- **Missing conversation state abstraction**: Raw `list[Message]` with ad-hoc mutations.
- **No clear plugin boundary**: `_ref_*` dirs suggest experimentation without a promotion path.
- **MCP bridge under-designed**: Doesn't implement actual MCP protocol (JSON-RPC 2.0 lifecycle).

---

## 2. Core Engine

### 2.1 Query Loop (`vibe/core/query_loop.py`)

**Bugs:**
- **System message accumulation**: On every `run()` with `initial_query`, planner injects another system message at index 0. Interactive mode accumulates them.
- **Tool calls dropped during compaction** (Line 153-156): Reconstruction only copies `role` and `content`; `tool_calls` are silently lost.
- **Missing `tool_call_id`**: Tool result messages don't include `tool_call_id`, breaking OpenAI-compatible providers.
- **String-matching fallback** (Line 300): `"not found"` substring match can falsely trigger MCP fallback on legitimate errors.

**Design:**
- State machine is underutilized (observation only, no flow control).
- `register_tool_handler` exists but is never used.
- `max_iterations=50` with no token budget cap.

### 2.2 Model Gateway (`vibe/core/model_gateway.py`)

**Issues:**
- Hardcodes `http://ai-api.applesay.cn` and `qwen3.5-plus` as defaults.
- Fresh `httpx.AsyncClient` per instance — no connection reuse in soak tests.
- No streaming support.
- `structured_output` does fragile string parsing to strip markdown fences.
- No request cancellation on `stop()`.

### 2.3 Context Compactor (`vibe/core/context_compactor.py`)

**Bugs:**
- Token over-counting: `str(tool_calls)` includes Python dict syntax overhead.
- **"summarize_middle" is misleading**: It deletes middle messages; no summarization happens.
- `_truncate` caps `content` at 4000 chars but ignores `tool_calls` payload.

**Design:**
- No pluggable strategy interface.
- `max_tokens=8000` default is low for modern context windows.

### 2.4 Error Recovery (`vibe/core/error_recovery.py`)

**Critical Bug:**
- Default `retryable_exceptions=(Exception,)` catches `asyncio.CancelledException` (since Python 3.8). Cancelled tasks are retried instead of propagating.

**Issues:**
- Jitter can make first retry as short as 0.5s.
- Swallows tracebacks on non-retryable exceptions.

### 2.5 Health Check (`vibe/core/health_check.py`)

**Issues:**
- Sends paid LLM requests (`max_tokens=1`) for health checks. Expensive and slow.
- `resolve_with_retry` logic is convoluted and re-checks the default model redundantly.

### 2.6 Config (`vibe/core/config.py`)

**Issues:**
- `Path.home()` can fail in containers without `$HOME`.
- Stores plaintext API keys on disk without warning.
- `_parse_list` error message is unhelpful for YAML string inputs.

---

## 3. Tool System

### 3.1 Tool System Base (`vibe/tools/tool_system.py`)

- Confusing schema naming (`get_schema` returns parameters, but `ToolSystem` wraps it).
- Swallows all exceptions without tracebacks.

### 3.2 Bash Tool (`vibe/tools/bash.py`)

**Security Issues:**
- `subprocess.run(shell=True)` blocks the event loop and is inherently dangerous.
- Denylist is incomplete (`python -c '...'`, `find ... -exec rm`, `perl -e` bypass it).
- Error messages leak internal regex patterns to the LLM.

**Issues:**
- stderr concatenated into stdout — no programmatic separation.

### 3.3 File Tools (`vibe/tools/file.py`)

**Issues:**
- No path jail / chroot. `resolve()` follows symlinks.
- No file size limits. Can crash on 1GB files or fill the disk.
- No patch/edit tool — only read and overwrite.

### 3.4 MCP Bridge (`vibe/tools/mcp_bridge.py`)

**Issues:**
- Mutable default anti-pattern in `MCPServerConfig`.
- **Not actual MCP protocol**: Uses arbitrary JSON POST instead of JSON-RPC 2.0 `tools/call`.
- Stdio transport spawns process per call, killing stateful servers.
- No `initialize` or `tools/list` capability negotiation.

### 3.5 Skill Manage Tool (`vibe/tools/skill_manage.py`)

- `create` fails if skill exists with no way to check first.
- Only writes `SKILL.md`; can't create linked files (`references/`, `scripts/`).

---

## 4. Harness Layer

### 4.1 Planner (`vibe/harness/planner.py`)

- Naive keyword matching with arbitrary weights.
- Falls back to ALL tools when none match, defeating optimization.
- `reasoning` field is built but never consumed.

### 4.2 Feedback Engine (`vibe/harness/feedback.py`)

- Self-verify asks the same LLM to critique itself — unreliable.
- Failed feedback returns neutral 0.5 score, masking real problems.
- Weak prompt engineering for JSON extraction.

### 4.3 Constraints (`vibe/harness/constraints.py`)

- Shallow argument merge in pre-hooks.
- Post-hooks ignore `outcome.allow`.
- `policy_hook` does substring matching (blocks `"echo 'sudo is not allowed'"`).

### 4.4 Instructions (`vibe/harness/instructions.py`)

- Doesn't recurse into skill subdirectories.
- `_parse_frontmatter` splits on `---` which appears in markdown horizontal rules.

### 4.5 Trace Store (`vibe/harness/memory/trace_store.py`)

- Sync `SentenceTransformer` download blocks the event loop.
- Embeddings stored as pickle BLOBs — unportable, unqueryable.
- Brute-force cosine similarity loads all embeddings into memory.
- All DB ops are synchronous, blocking the async event loop.

---

## 5. Eval Framework

### 5.1 Eval Runner (`vibe/evals/runner.py`)

**Issues:**
- `run_all` is sequential. Cases are independent and should run in parallel.
- Assertion closures mutate `passed` and `diff` via `nonlocal` — hard to test in isolation.

### 5.2 Observability (`vibe/evals/observability.py`)

- Span finish logic is O(n) and can pick wrong parent if finished out of order.
- `_metrics` list grows unbounded during soak tests.
- `_percentile` interpolation is correct but fragile.

### 5.3 Soak Test (`vibe/evals/soak_test.py`)

- Signal handler may fail if called from non-main thread.
- `prompt_tokens` and `completion_tokens` always zero in snapshots.
- Checkpoint overwrites instead of appends (data loss on crash).

### 5.4 Multi-Model Runner (`vibe/evals/multi_model_runner.py`)

- Fresh `QueryLoop` per case = no connection reuse.
- `best_overall` only considers pass rate, ignoring cost and latency.
- `by_tag` aggregation does expensive O(M×N×T) lookups.

### 5.5 Model Registry (`vibe/evals/model_registry.py`)

- Mutable default anti-pattern in `ModelProfile`.
- `get_fallback_chain` behavior differs significantly when `config` is passed vs None.

---

## 6. CLI & Entry Points

### `vibe/cli/main.py`

- `allow_extra_args=True` breaks help generation.
- CLI creates stripped-down `QueryLoop` missing planner, feedback, MCP, trace store.
- Duplicated LLM setup logic.

### `run_e2e_evals.py`

- `MockLLM` is 120 lines of inline heuristics with interface mismatches.
- Duplicated `QueryLoop` wiring (no shared factory).
- Benchmark mode fails CI if any model doesn't get perfect score — too strict.

---

## 7. Testing Strategy

### Gaps
- No integration tests for real LLM calls.
- **Zero tests** for: MCP bridge, feedback engine, soak test, observability, health check.
- No tests for config loading, path expansion, YAML parsing.
- No performance tests.

---

## 8. Security Summary

| Risk | Severity | Location |
|------|----------|----------|
| `subprocess.run(shell=True)` | **High** | `bash.py:130` |
| Bypassable denylist | **High** | `bash.py:14-36` |
| No file size limits | **Medium** | `file.py` |
| Path resolution without jail | **Medium** | `file.py:84` |
| Plaintext API key in config | **Medium** | `config.py:136` |
| Cancellation caught as retryable | **Medium** | `error_recovery.py:17` |
| Regex leakage in errors | **Low** | `bash.py:119` |

---

## 9. Performance Concerns

1. **Sync SQLite in async path**: Blocks event loop.
2. **No HTTP connection pooling**: Fresh client per case.
3. **O(n) token estimation per turn**.
4. **Brute-force vector search**: Loads all embeddings.
5. **Unbounded observability metrics list**.

---

## 10. Top 10 Specific Bugs

1. Tool calls dropped during compaction (`query_loop.py:153-156`)
2. Cancellation exception retried (`error_recovery.py:44`)
3. Post-hooks ignore `allow` flag (`constraints.py:71-76`)
4. Health check costs API calls (`health_check.py:42-65`)
5. `run_all` is sequential (`runner.py:324-325`)
6. `MockLLM` interface mismatch (`run_e2e_evals.py:47-121`)
7. Signal handler thread safety (`soak_test.py:100-105`)
8. Checkpoint overwrites instead of appends (`soak_test.py:222-235`)
9. `prompt_tokens` always zero in soak (`soak_test.py:145-152`)
10. Missing `tool_call_id` in tool messages (`query_loop.py:209-215`)

---

## 11. Recommendations

### Immediate (correctness / crashes)
1. Fix cancellation retry bug in `error_recovery.py`
2. Preserve `tool_calls` during context compaction
3. Add `tool_call_id` to tool result messages
4. Switch BashTool to async subprocess (or executor)
5. Make `run_all` parallel with concurrency limits

### Short-term (quality / safety)
6. Extract `QueryLoopFactory` to unify wiring
7. Add file size limits to file tools
8. Implement real MCP protocol lifecycle
9. Add streaming support to `LLMClient`
10. Replace brute-force vector search with `sqlite-vss`
11. Write tests for all untested modules

### Medium-term (architecture)
12. Refactor QueryLoop into smaller objects (`ConversationState`, `TurnExecutor`)
13. Add real summarization strategy to compactor
14. Implement request cancellation propagation
15. Add patch/edit file tool
16. Security hardening: AST-based command analysis or containerization
