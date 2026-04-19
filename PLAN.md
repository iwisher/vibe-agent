# Vibe-Agent Eval Infrastructure — Unified Plan

## Status: Draft — Awaiting Review & Approval

---

## ✅ Already Delivered (Foundation)

| Item | File | Status |
|------|------|--------|
| End-to-end eval runner | `run_e2e_evals.py` | ✅ Working — 10/10 cases pass |
| Eval runner core | `vibe/evals/runner.py` | ✅ With `stdout_contains` bugfix |
| Eval store (SQLite) | `vibe/harness/memory/eval_store.py` | ✅ |
| 10 builtin eval cases | `vibe/evals/builtin/*.yaml` | ✅ |
| **Model Registry** | `vibe/evals/model_registry.py` | ✅ Skeleton — 1 default Ollama profile |
| **Multi-Model Runner** | `vibe/evals/multi_model_runner.py` | ✅ Skeleton — untested end-to-end |
| **Soak Test** | `vibe/evals/soak_test.py` | ✅ Skeleton — untested |
| **Observability** | `vibe/evals/observability.py` | ✅ Skeleton — untested |

---

## 📝 Proposed Tasks

### PHASE 1: Stabilize What Exists (No New Features)

**1.0 Benchmark End-to-End Validation**
- Run `default` model against all 10 cases
- Fix any runtime bugs (API key flow, QueryLoop lifecycle, LLM errors)
- Generate first real scorecard (JSON + Markdown)
- Success: model runs, scorecard saved to `~/.vibe/scorecards/`

**1.1 Soak Test Short Run**
- Run 10-minute soak with `default` model
- Verify checkpoint JSONL writes correctly
- Verify degradation detection works
- Success: report + summary saved, no crashes

**1.2 Observability Integration Check**
- Verify spans are collected during eval runs
- Verify metrics export produces valid JSON
- Success: trace + metrics files contain meaningful data

---

### PHASE 2: Expand Eval Suite

**2.1 MCP Bridge Evals (3 cases)**
- `mcp_bridge_001`: MCP tool discovery works
- `mcp_bridge_002`: MCP tool execution succeeds
- `mcp_bridge_003`: MCP failure falls back gracefully
- Requires: a mock MCP server or lightweight real one

**2.2 Context Planner Evals (3 cases)**
- `planner_001`: Correct tool selection for query
- `planner_002`: Does NOT over-select irrelevant tools
- `planner_003`: Skill matching accuracy

**2.3 Context Compactor Evals (2 cases)**
- `compactor_001`: Compaction triggers when context > max_tokens
- `compactor_002`: Key info from early messages survives compaction

**2.4 Feedback Engine Evals (2 cases)**
- `feedback_001`: Low score triggers self-correction retry
- `feedback_002`: High score completes without retry

**2.5 Edge & Stress Evals (3 cases)**
- `edge_001`: Empty tool result handling
- `edge_002`: Malformed tool call arguments (JSON parse recovery)
- `edge_003`: Max iteration limit exhaustion

**2.6 Eval Runner Assertions**
- Add `tool_sequence` assertion (verify exact order)
- Add `no_tool_called` assertion
- Add `metrics_threshold` assertion (latency/token bounds)

**2.7 Suite Governance**
- Create `EVAL_SUITE.md` documenting each case
- Add tag validation in `EvalStore.load_builtin_evals()`
- Enforce: every case has `subsystem` + `difficulty` + `category` tags

**Target: 23+ total cases**

---

### PHASE 3: Multi-Model Benchmarking

**3.1 Model Registry Hardening**
- Test default model via configurable endpoint
- Add fallback chain: primary → same-provider → default
- Cost estimation: token usage × cost_per_1k

**3.2 Multi-Model Runner Hardening**
- Parallel mode support
- Graceful handling of per-model failures (don't kill whole benchmark)
- Token usage aggregation per model

**3.3 Scorecard Generator**
- JSON report + Markdown table
- Per-tag breakdown
- Cost per run

**3.4 Baseline Scorecard**
- Run all models against 23-case suite
- Save to `docs/baseline_scorecard_2026-MM-DD.json`

**3.5 CLI Integration**
- `python run_e2e_evals.py benchmark --models ...`
- Already skeleton exists — harden and document

---

### PHASE 4: Observability

**4.1 EvalRunner Instrumentation**
- Span: `eval_case` (parent)
- Span: `llm_call` (child)
- Span: `tool_execution` (child)
- Span: `assertion_check` (child)
- Metric: `eval_latency` histogram per case
- Metric: `eval_passed` counter per case
- Metric: `llm_token_usage` gauge

**4.2 Trace Validation**
- Every eval run produces a complete trace
- Parent-child relationships correct
- No orphaned spans

**4.3 Metrics Aggregation**
- Histogram p50/p95/p99 computed correctly
- Counter increments verified
- Export JSON schema stable

**4.4 Soak Test + Observability Integration**
- Soak test uses observability for all internal timing
- Per-iteration metrics visible in real-time

---

### PHASE 5: CI Integration

**5.1 GitHub Actions Workflow**
- `.github/workflows/eval.yml`
- Trigger: PR, main push, manual dispatch
- Job chain: lint → unit-test → eval

**5.2 Baseline Management**
- `docs/baseline_scorecard.json` committed to repo
- `update_baseline.py` — update from latest run

**5.3 Regression Detector**
- Fail CI if `overall.score < baseline * 0.95`
- Fail if any holdout case passes→fails

**5.4 PR Comment Bot**
- Post markdown score diff as PR comment

**5.5 README Badges**
- "Evals: X/Y passing" badge

**5.6 Cost-Aware CI**
- CI uses cheapest model (`is_ci_model=True`)
- Skip on draft PRs
- `[skip eval]` commit message support

---

### PHASE 6: Long-Running Soak Test

**6.1 Soak Test Full Run**
- 1-hour continuous run
- Tracks: pass rate, latency drift, memory, error patterns

**6.2 Token Metrics in Soak**
- Wire token usage from LLMResponse into SoakSnapshot
- Cost per hour estimate

**6.3 Memory Leak Detection**
- Verify QueryLoop cleanup between iterations
- Track RSS over time

**6.4 Soak Report Dashboard**
- Time-series chart data (JSON for external viz)
- Degradation alert thresholds

---

## 📅 Execution Order

```
PHASE 1: Stabilize (1.0–1.2)
  └── Validate benchmark, soak, observability actually work

PHASE 2: Expand Suite (2.1–2.7)
  └── Grow from 10 → 23+ cases with full subsystem coverage

PHASE 3: Benchmark (3.1–3.5)
  └── Multi-model comparison with real scorecards

PHASE 4: Observability (4.1–4.4)
  └── Instrument everything, validate traces

PHASE 5: CI (5.1–5.6)
  └── GitHub Actions, regression gates, badges

PHASE 6: Soak (6.1–6.4)
  └── 1-hour runs, memory monitoring, dashboards
```

## ✅ Success Criteria

| Metric | Target |
|--------|--------|
| Eval cases | ≥ 23 |
| Subsystems covered | 8/8 |
| Models benchmarked | ≥ 3 |
| CI gate | Blocks merge on regression |
| Soak stability | 1-hour run ≥ 95% pass rate |
| Trace completeness | 100% of eval runs fully traced |

---

## 💰 Budget / Cost Awareness

- LLM endpoint: configure via `VIBE_BASE_URL` and `VIBE_MODEL`
- Cost estimates built into ModelProfile
- CI uses cheapest model by default
- Soak test cost = (cases/min × minutes × avg_tokens × cost_per_1k)
