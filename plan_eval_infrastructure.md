# Vibe-Agent Eval Infrastructure Plan
## Tasks: 1 (More Eval Cases) + 3 (Multi-Model Comparison) + 4 (CI Integration)

═══════════════════════════════════════════════════════════════════════════════

## TASK 1: Expand Eval Suite (更多 Eval Cases)

### 🎯 Goal
Build a comprehensive, layered eval suite that exercises every subsystem of vibe-agent
and establishes a "scorecard" for hill-climbing optimization.

### ❌ No Goal (Out of Scope)
- **NOT** about writing perfect prompts that always pass — eval cases should reflect
  real user tasks, including edge cases that *currently* fail
- **NOT** a one-time exercise — the suite must be designed for continuous growth
- **NOT** unit tests for pure functions — these are end-to-end behavioral evals

### 📋 Key Requirements
1. **Coverage Matrix**: Every eval case must map to a subsystem:
   | Subsystem | Test Target | Current Cases |
   |-----------|-------------|---------------|
   | Tool Selection | Planner picks right tools for task | tool-selection-001 |
   | Tool Execution | Real bash/file ops work correctly | bash-math, file-read, etc. |
   | Error Recovery | Retry + fallback on failures | error-recovery-001 |
   | Security Hooks | Dangerous commands blocked | security-hook-001 |
   | Multi-Step Reasoning | Chain multiple tool calls | multi-step-001/002 |
   | Instruction Following | Exact formatting compliance | instruction-following-001 |
   | **MCP Bridge** | External tool calls succeed | ❌ **MISSING** |
   | **Context Planner** | Tool/skill/MCP selection accuracy | ❌ **MISSING** |
   | **Context Compactor** | Info retained after compaction | ❌ **MISSING** |
   | **Feedback Engine** | Self-correction improves output | ❌ **MISSING** |

2. **Assertion Types**: Each case must use at least one of:
   - `file_exists` + `file_contains` + `contains_text` → filesystem state
   - `stdout_contains` → tool output (includes error field post-bugfix)
   - `response_contains` → LLM final response text
   - **NEW**: `tool_sequence` → exact tool call ordering
   - **NEW**: `no_tool_called` → verify certain tools are NOT used
   - **NEW**: `metrics_threshold` → latency / token usage bounds

3. **Determinism**: Every case must be:
   - Self-contained (creates its own inputs)
   - Idempotent (safe to re-run, cleanup after or use temp dirs)
   - Deterministic (same input → same expected output)

4. **Holdout Split**: Minimum 20% of cases marked `holdout_set: true` to prevent overfitting

5. **Tagging System**: Every case must have tags for filtering:
   Required tags: `subsystem_name`, `difficulty` (`easy`/`medium`/`hard`), `category`

### 📝 Specific Instructions (Sub-tasks)

```
1.1  MCP Bridge Evals
     ├── Create eval cases for MCP tool discovery, execution, and error handling
     ├── Mock an MCP server locally (stdio/sse) or use a real lightweight one
     ├── Target: 3 cases (discovery, success, failure-fallback)
     └── Files: vibe/evals/builtin/mcp_bridge_001.yaml, _002.yaml, _003.yaml

1.2  Context Planner Evals
     ├── Test that planner selects correct tools based on query keywords
     ├── Test that planner does NOT include irrelevant tools (over-selection penalty)
     ├── Test skill matching accuracy
     └── Target: 3 cases (correct-select, under-select, over-select)

1.3  Context Compactor Evals
     ├── Feed long conversation > max_tokens, verify compactor triggers
     ├── Verify key information from early messages is still retrievable after compaction
     ├── Target: 2 cases (compaction-triggers, info-retention)

1.4  Feedback Engine Evals
     ├── Test self-correction loop: inject a bad response, verify feedback triggers retry
     ├── Test that feedback score below threshold causes additional iteration
     └── Target: 2 cases (feedback-triggered-retry, no-feedback-needed)

1.5  Edge Case & Stress Evals
     ├── Empty tool result handling
     ├── Malformed tool call arguments (JSON parse error recovery)
     ├── Maximum iteration limit exhaustion
     ├── Concurrent tool calls (if supported)
     └── Target: 3 cases

1.6  Eval Runner Enhancement
     ├── Add `tool_sequence` assertion: verify exact order of tool calls
     ├── Add `no_tool_called` assertion: verify tool NOT in call list
     ├── Add `metrics_threshold` assertion: fail if latency/tokens exceed bound
     └── File: vibe/evals/runner.py

1.7  Suite Governance
     ├── Create EVAL_SUITE.md documenting each case's purpose and subsystem mapping
     ├── Enforce tag validation in EvalStore.load_builtin_evals()
     └── Target: 20+ total cases by end of task 1
```

═══════════════════════════════════════════════════════════════════════════════

## TASK 3: Multi-Model Benchmark (多模型对比)

### 🎯 Goal
Run the identical eval suite across multiple LLM providers/models and produce a
comparative scorecard that informs model selection for different task types.

### ❌ No Goal (Out of Scope)
- **NOT** about finding "the best model" — different models may excel at different tags
- **NOT** about fine-tuning or prompt engineering per model — same prompts, same tools
- **NOT** about cost optimization — though metrics should capture cost proxies (tokens)
- **NOT** about latency benchmarking under load — single-threaded, reproducible runs only

### 📋 Key Requirements
1. **Model Matrix**: Must support running against:
   - qwen3.5-plus (current baseline)
   - kimi-k2.5 (via kimi.com/coding)
   - GPT-4o / GPT-4o-mini (via OpenRouter or direct)
   - Claude Sonnet 4 / Haiku 4 (via OpenRouter)
   - Local models (Ollama/LM Studio via local endpoint)

2. **Identical Conditions**: Same eval cases, same tool system, same timeout,
   same temperature, same max_iterations for every model

3. **Comparable Metrics**: For each model x case combination, capture:
   - Pass/Fail
   - Latency (seconds)
   - Token usage (prompt + completion)
   - Number of tool calls made
   - Number of LLM turns (iterations)
   - Retry count (from ErrorRecovery)

4. **Scorecard Output**: Must produce a structured report:
   ```json
   {
     "model": "qwen3.5-plus",
     "overall": {"passed": 18, "failed": 2, "score": 0.90},
     "by_tag": {
       "security": {"passed": 2, "failed": 0},
       "multi_step": {"passed": 4, "failed": 1}
     },
     "by_difficulty": {
       "easy": {"avg_latency": 3.2, "score": 1.0},
       "hard": {"avg_latency": 12.5, "score": 0.6}
     }
   }
   ```

5. **Reproducibility**: Each run must be tagged with:
   - Exact model name + provider
   - Eval suite git commit hash
   - Timestamp
   - Random seed (if temperature > 0)

### 📝 Specific Instructions (Sub-tasks)

```
3.1  Model Registry & Config
     ├── Create MODEL_REGISTRY.yaml or extend config.yaml with model profiles
     ├── Each profile: name, base_url, api_key_env_var, model_id, cost_per_1k_tokens
     ├── Support both direct endpoints and OpenRouter routing
     └── File: vibe/evals/model_registry.py or config addition

3.2  Multi-Model Runner
     ├── Extend run_e2e_evals.py or create run_multi_model_evals.py
     ├── Loop: for each model profile → create fresh QueryLoop → run all cases
     ├── Parallel option: run models concurrently (configurable)
     ├── Capture per-case metrics in EvalStore with model attribution
     └── File: vibe/evals/multi_model_runner.py

3.3  Metrics Enhancement
     ├── Add metrics fields to EvalResult dataclass
     ├── Capture: latency, prompt_tokens, completion_tokens, tool_call_count, turn_count
     ├── Store in eval_results table with model column
     └── Files: vibe/harness/memory/eval_store.py, vibe/evals/runner.py

3.4  Scorecard Generator
     ├── Query EvalStore and aggregate by model/tag/difficulty
     ├── Output: JSON report + markdown table for human reading
     ├── Highlight best/worst per category
     └── File: vibe/evals/scorecard.py

3.5  CLI Integration
     ├── Add `/eval benchmark` slash command to vibe CLI
     ├── Args: --models "qwen3.5-plus,kimi-k2.5" --parallel --output report.md
     └── File: vibe/cli/main.py

3.6  Baseline Run
     ├── Run current 20-case suite against at least 3 models
     ├── Produce first comparative scorecard
     └── Deliverable: docs/baseline_scorecard_2026-04-17.md
```

═══════════════════════════════════════════════════════════════════════════════

## TASK 4: CI Integration (GitHub Actions)

### 🎯 Goal
Every PR and commit to main automatically runs the eval suite, reports results,
and blocks merge if the score regresses below the baseline.

### ❌ No Goal (Out of Scope)
- **NOT** about setting up full test coverage for unit tests — this is specifically
  for the end-to-end eval suite
- **NOT** about deploying the agent itself — only CI for eval
- **NOT** about running evals on every push to every branch — only PRs + main
- **NOT** about cost optimization — eval runs cost API tokens; we'll use cheap models for CI

### 📋 Key Requirements
1. **Trigger Conditions**:
   - Pull Request: run eval suite on PR branch
   - Main branch push: run eval suite + update baseline badge
   - Manual dispatch: allow running specific models/tags

2. **Job Structure**:
   ```
   ci-eval:
     needs: [lint, unit-tests]  # only run if unit tests pass
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
       - checkout
       - setup Python + deps
       - run eval suite (cheap model: qwen3.5-plus or gpt-4o-mini)
       - upload results artifact
       - post PR comment with score diff vs baseline
   ```

3. **Baseline Comparison**:
   - Store baseline scorecard as artifact or in `docs/baseline_scorecard.json`
   - Compare current run vs baseline: show ↑↓ deltas
   - Regression gate: fail CI if `overall.score` drops by > 5% or any `holdout_set` case fails

4. **Secrets Management**:
   - API keys stored in GitHub Secrets (`LLM_API_KEY`, `LLM_BASE_URL`)
   - No hardcoded keys in repo (already fixed from gemini audit!)
   - Support multiple provider keys: `APPLEAY_API_KEY`, `KIMI_API_KEY`, `OPENROUTER_API_KEY`

5. **Reporting**:
   - PR comment with markdown table of results
   - Badge in README showing "Evals: 18/20 passing"
   - Link to detailed artifact for debugging

6. **Cost Control**:
   - Use cheapest model that passes all cases for CI (fallback chain)
   - Skip evals on draft PRs (configurable)
   - Max 2 eval runs per PR (initial + after approval)

### 📝 Specific Instructions (Sub-tasks)

```
4.1  GitHub Actions Workflow
     ├── Create .github/workflows/eval.yml
     ├── Define job: test → lint → eval (needs test)
     ├── Use matrix strategy for Python versions (3.11, 3.12)
     ├── Upload eval_results.db as artifact
     └── File: .github/workflows/eval.yml

4.2  Baseline Management
     ├── Create docs/baseline_scorecard.json (committed to repo)
     ├── Script to update baseline: update_baseline.py --from-latest-run
     ├── Baseline updated only on main branch merges, not PRs
     └── File: vibe/evals/update_baseline.py

4.3  Regression Detector
     ├── Compare current run results against baseline JSON
     ├── Rules:
     │   - FAIL if overall.score < baseline.score * 0.95
     │   - FAIL if any holdout case that previously passed now fails
     │   - WARN if any optimization case drops but holdout stays OK
     ├── Exit code 1 on regression, 0 on improvement/stable
     └── File: vibe/evals/regression_check.py

4.4  PR Comment Bot
     ├── Use GitHub Actions bot or existing PR comment action
     ├── Format: markdown table with ✅/❌ + score diff
     ├── Include link to artifact for full details
     └── Inline in .github/workflows/eval.yml or separate action

4.5  README Badges
     ├── Add eval score badge to README.md
     ├── Update badge text on main branch runs via workflow commit
     └── File: README.md (badge line)

4.6  Cost-Aware CI Config
     ├── Add `ci_model` field to model registry — cheapest model for CI
     ├── Support skipping evals: [skip eval] in commit message
     ├── Support draft PR detection (skip evals on drafts)
     └── File: vibe/evals/ci_config.py

4.7  Makefile Targets
     ├── `make eval` → run local eval (same as run_e2e_evals.py)
     ├── `make eval-baseline` → update baseline
     ├── `make eval-compare` → compare current vs baseline
     └── File: Makefile
```

═══════════════════════════════════════════════════════════════════════════════

## 📅 Execution Order & Dependencies

```
Phase 1: Foundation (Tasks 1.1–1.7)
    ├── Build eval suite to 20+ cases
    ├── Add new assertion types to runner
    └── Write EVAL_SUITE.md documentation

Phase 2: Benchmarking (Tasks 3.1–3.6)
    ├── Build model registry
    ├── Run multi-model comparison
    └── Produce first baseline scorecard

Phase 3: Automation (Tasks 4.1–4.7)
    ├── Set up GitHub Actions workflow
    ├── Configure regression detection
    └── Add badges + PR comments

Phase 4: Iterate
    ├── Use CI feedback to add more cases (back to Phase 1)
    └── Tune model selection based on benchmark results
```

## 📊 Success Criteria

| Metric | Target |
|--------|--------|
| Eval cases | ≥ 20 |
| Subsystems covered | 8/8 (all) |
| Holdout ratio | ≥ 20% |
| Models benchmarked | ≥ 3 |
| CI pass/fail gate | Working on PRs |
| Regression detection | Auto-block on 5% score drop |
| Scorecard generation | < 1s after eval completion |
