# Vibe-Agent — Unified Remaining Work Plan

**Status:** Post-Phase-5 (Decoupling Complete)  
**Current state:** 231 tests, 1 failing, 3 warnings, 32 eval cases  
**Date:** 2026-04-18  
**Critique source:** Claude Code independent review

---

## Phase 0: Bug Fixes (P0 from Critique) 🔴

**Goal:** Fix all critical/security/stability bugs before any new features.  
**Estimated:** 1.5–2 hours  
**Dependency:** None  
**Exit criteria:** All 231 tests pass, zero warnings, zero security gaps

- [ ] **0.1 Fix AsyncMock trap in `test_model_gateway.py`**
  - Locations: lines 30, 88, 114
  - `response.raise_for_status()` is sync, not async
  - Fix: change `AsyncMock()` to `MagicMock()` for `raise_for_status`
  - **Tests:** Run `pytest tests/test_model_gateway.py -v`, verify zero warnings

- [ ] **0.2 Fix `test_vector_similarity_search` skip logic**
  - File: `tests/test_trace_store.py:45-75`
  - Add `@pytest.mark.skipif(not HAS_SENTENCE_TRANSFORMERS, ...)` or mock embedding
  - **Tests:** Run `pytest tests/test_trace_store.py -v`, verify passes without `sentence-transformers`

- [ ] **0.3 Fix file tool path traversal in `_redirect_path`**
  - File: `vibe/tools/file.py:15-24`
  - Bug: `/tmp/vibe_../etc/passwd` escapes work dir
  - Fix: after redirect, validate resolved path stays within `VIBE_EVAL_WORK_DIR` using `os.path.realpath() + startswith()` (same pattern as `bash.py`)
  - **Tests:** Add `test_file_redirect_path_traversal_blocked`

- [ ] **0.4 Fix TOCTOU in `skill_manage.py`**
  - File: `vibe/tools/skill_manage.py:62-82`
  - Bug: validates `resolved` path but writes to original `skill_dir` variable; symlink swap = write outside bounds
  - Fix: use the resolved path for all I/O, not the original variable
  - **Tests:** Add `test_skill_manage_toctou_symlink_swap_blocked`

---

## Phase A: Stabilization & Polish (P1 from Critique + Milestone 1.3)

**Goal:** Close stability gaps, config/CLI papercuts, type consistency.  
**Estimated:** 4–5 hours  
**Dependency:** Phase 0  
**Exit criteria:** All tests pass, clean mypy, no dead features

- [ ] **A1. QueryLoop cancellation safety**
  - File: `vibe/core/query_loop.py:141-220`
  - Bug: no `try/finally` to reset `_running` flag on `CancelledError`
  - Fix: wrap loop body in `try/finally`, ensure `_running = False` on all exit paths
  - **Tests:** Add `test_query_loop_cancellation_resets_state`

- [ ] **A2. Harden error classification in `ErrorRecovery.handle_error()`**
  - File: `vibe/core/error_recovery.py:80-95`
  - Bug: substring matching (`"timeout" in error_msg.lower()`) misclassifies unrelated errors
  - Fix: check exception type first, then fall back to substring; map specific `httpx` status codes
  - **Tests:** Add `test_handle_error_type_precedence`, `test_handle_error_no_false_positives`

- [ ] **A3. Unify typing styles to PEP 585**
  - File: `vibe/core/query_loop.py` (mixed `list[dict]` and `List[Dict]`)
  - Scope: sweep all files for `typing.List`, `typing.Dict`, `typing.Optional` → use builtins
  - Tool: `pyupgrade --py311-plus` or manual
  - **Tests:** Ensure mypy passes after cleanup

- [ ] **A4. Wire or delete dead `LLM_SUMMARIZE` strategy**
  - File: `vibe/core/context_compactor.py:127-139`
  - Issue: `summarize_fn` param exists but no caller passes one
  - Decision: **wire it** — add `summarize_fn` factory to `QueryLoopFactory` that calls `llm.structured_output()` with a summary schema
  - **Tests:** Add `test_compactor_llm_summarize_reduces_tokens`

- [ ] **A5. Fix factory default divergence**
  - File: `vibe/core/query_loop_factory.py`
  - Bug: `QueryLoopFactory` uses `max_iterations=10` while `QueryLoopConfig` default is `50`
  - Fix: read from `config.query_loop.max_iterations` consistently; remove magic numbers
  - **Tests:** Add `test_factory_uses_config_defaults`

- [ ] **A6. Configurable memory paths**
  - Support `VIBE_MEMORY_DIR` env var in `TraceStore` and `EvalStore`
  - Default remains `~/.vibe/memory/`
  - **Tests:** Add `test_memory_dir_env_override`

- [ ] **A7. Add `vibe traces` CLI command**
  - `vibe traces list --limit 20`
  - `vibe traces show <session_id>`
  - `vibe traces search <query>` (keyword + vector fallback)
  - **Tests:** Add `tests/test_cli_traces.py` using `click.testing.CliRunner`

- [ ] **A8. Minimal wiki memory (`vibe/harness/memory/wiki.py`)**
  - Markdown-based with `compiled_truth/` + `timeline/` split
  - Append-only timeline entries; periodic compilation into truth files
  - Loaded by `InstructionLoader` as a skill source
  - **Tests:** Add `test_wiki_compile`, `test_wiki_load_as_skill`

- [ ] **A9. Configurable compaction strategies**
  - Add `OffloadStrategy` (write old messages to disk, load on demand)
  - Add `DropStrategy` (drop old messages without summary)
  - Make strategy user-configurable via `VibeConfig`
  - **Tests:** Add strategy-specific unit tests

- [ ] **A10. Archive/remove `_ref_*` legacy dirs**
  - Root `_ref_core/`, `_ref_utils/`, `_ref_cw_core/`
  - Move anything still valuable into `docs/` or delete

---

## Phase B: Eval Suite Expansion (PLAN.md Phase 2)

**Goal:** Grow from 32 → 45+ cases with subsystem coverage.  
**Estimated:** 4–5 hours  
**Dependency:** Phase A (stable baseline)

- [ ] **B1. Context Planner evals (3 cases)**
  - `planner_001`: Correct tool selection for query
  - `planner_002`: Does NOT over-select irrelevant tools
  - `planner_003`: Skill matching accuracy

- [ ] **B2. Context Compactor evals (2 cases)**
  - `compactor_001`: Compaction triggers when context > max_tokens
  - `compactor_002`: Key info from early messages survives compaction

- [ ] **B3. Feedback Engine evals (2 cases)**
  - `feedback_001`: Low score triggers self-correction retry
  - `feedback_002`: High score completes without retry

- [ ] **B4. MCP Bridge evals (3 cases)**
  - Requires: lightweight mock MCP server (stdio or HTTP)
  - `mcp_bridge_001`: MCP tool discovery works
  - `mcp_bridge_002`: MCP tool execution succeeds
  - `mcp_bridge_003`: MCP failure falls back gracefully

- [ ] **B5. Edge & Stress evals (3 cases)**
  - `edge_001`: Empty tool result handling
  - `edge_002`: Malformed tool call arguments (JSON parse recovery)
  - `edge_003`: Max iteration limit exhaustion (state = INCOMPLETE)

- [ ] **B6. Suite governance**
  - Create `EVAL_SUITE.md` documenting each case
  - Add `vibe eval validate` CLI — checks all YAML cases have required tags

---

## Phase C: Multi-Model Benchmarking (PLAN.md Phase 3)

**Goal:** Harden existing skeletons, produce real scorecards.  
**Estimated:** 4–5 hours  
**Dependency:** Phase B (stable eval suite)

- [ ] **C1. Model Registry hardening**
  - Test default model via configurable endpoint
  - Add fallback chain: primary → same-provider → default
  - Cost estimation: token usage × cost_per_1k

- [ ] **C2. Multi-Model Runner hardening**
  - Parallel mode support
  - Graceful per-model failures (don't kill whole benchmark)
  - Token usage aggregation per model

- [ ] **C3. Scorecard Generator**
  - JSON report + Markdown table
  - Per-tag breakdown
  - Cost per run

- [ ] **C4. Baseline Scorecard**
  - Run all models against expanded suite
  - Save to `docs/baseline_scorecard_2026-MM-DD.json`

- [ ] **C5. CLI Integration**
  - `vibe eval benchmark --models model1,model2`
  - Scorecard output to stdout + file

---

## Phase D: Observability Hardening (PLAN.md Phase 4)

**Goal:** Instrument everything, validate traces, export real data.  
**Estimated:** 3–4 hours  
**Dependency:** Phase C (benchmarking generates load)

- [ ] **D1. EvalRunner instrumentation completeness**
  - Ensure ALL spans/metrics are populated end-to-end
  - Verify `Observability` instances are passed through `EvalRunner` correctly

- [ ] **D2. Trace validation**
  - Every eval run produces a complete trace
  - Parent-child relationships correct
  - No orphaned spans

- [ ] **D3. Metrics aggregation validation**
  - Histogram p50/p95/p99 computed correctly
  - Counter increments verified
  - Export JSON schema stable

- [ ] **D4. Soak Test + Observability Integration**
  - Soak test uses observability for all internal timing
  - Per-iteration metrics visible in real-time

---

## Phase E: CI Integration (PLAN.md Phase 5)

**Goal:** Block merge on regression.  
**Estimated:** 3–4 hours  
**Dependency:** Phase D (observability produces scorecards)

- [ ] **E1. GitHub Actions Workflow**
  - `.github/workflows/eval.yml`
  - Trigger: PR, main push, manual dispatch
  - Job chain: lint → unit-test → eval

- [ ] **E2. Baseline Management**
  - `docs/baseline_scorecard.json` committed to repo
  - `vibe eval update-baseline` — update from latest run

- [ ] **E3. Regression Detector**
  - Fail CI if `overall.score < baseline * 0.95`
  - Fail if any holdout case passes→fails

- [ ] **E4. Cost-Aware CI**
  - CI uses cheapest model (`is_ci_model=True` in profile)
  - Skip on draft PRs
  - `[skip eval]` commit message support

---

## Phase F: Architecture Polish (P2 from Critique — Nice to Have)

**Goal:** Address structural debt without changing behavior.  
**Estimated:** 4–6 hours  
**Dependency:** Phase E (all functional work complete)

- [ ] **F1. Extract QueryLoop coordinators**
  - File: `vibe/core/query_loop.py` (350+ lines)
  - Extract: `ToolExecutor`, `FeedbackCoordinator`, `CompactionCoordinator`
  - Keep `QueryLoop.run()` as thin orchestrator (< 40 lines)
  - **Tests:** Ensure zero functional change, all tests pass

- [ ] **F2. Connection pooling for LLMClient**
  - Issue: each `LLMClient` creates its own `httpx.AsyncClient`
  - Fix: accept optional shared `AsyncClient` or connection pool in factory
  - **Tests:** Add `test_client_reuse_across_eval_runs`

- [ ] **F3. Document planner as placeholder**
  - File: `vibe/harness/planner.py`
  - Add docstring explaining keyword matching is v1; LLM-based planner is future work
  - No code change needed

- [ ] **F4. Remove remaining Hermes branding**
  - Files: `skill_manage.py`, `config.py`, eval YAMLs
  - Replace references to `~/.hermes/` with `~/.vibe/`
  - Update comments/docstrings

---

## Phase G: Long-Running Soak Test (PLAN.md Phase 6)

**Goal:** Prove stability over time.  
**Estimated:** 2–3 hours  
**Dependency:** Phase D (observability integration)

- [ ] **G1. Soak Test Full Run**
  - 1-hour continuous run
  - Tracks: pass rate, latency drift, memory, error patterns

- [ ] **G2. Token Metrics in Soak**
  - Wire token usage from LLMResponse into SoakSnapshot
  - Cost per hour estimate

- [ ] **G3. Memory Leak Detection**
  - Verify QueryLoop.cleanup between iterations
  - Track RSS over time

- [ ] **G4. Soak Report Dashboard Data**
  - Time-series chart data (JSON for external viz)
  - Degradation alert thresholds

---

## Execution Order

```
Phase 0: Bug Fixes (security + stability)
    │
Phase A: Stabilization & Polish (config, CLI, types, dead features)
    │
Phase B: Eval Suite Expansion
    │
Phase C: Multi-Model Benchmarking
    │
Phase D: Observability Hardening
    │
Phase E: CI Integration
    │
Phase F: Architecture Polish (refactoring)
    │
Phase G: Long-Running Soak Test
```

---

## Success Criteria

| Metric | Current | Target |
|--------|---------|--------|
| Tests passing | 225/231 (97%) | 231/231 (100%) |
| Pytest warnings | 3 | 0 |
| Security gaps | 2 (file traversal, TOCTOU) | 0 |
| Eval cases | 32 | ≥ 45 |
| Subsystems with eval coverage | 4/8 | 8/8 |
| Models benchmarked | 1 | ≥ 3 |
| CI gate | None | Blocks merge on regression |
| Soak stability | Untested | 1-hour run ≥ 95% pass rate |
| Trace completeness | Partial | 100% of eval runs fully traced |

---

## Review Gates

Per user's preferred workflow:
1. ✅ Plan reviewed by Claude Code (independent critique incorporated above)
2. **User approval required before implementation**
3. After EACH phase: code review via Gemini CLI
4. User approval required before next phase
