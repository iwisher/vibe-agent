# Evaluation Suite

Vibe Agent includes a comprehensive evaluation infrastructure to ensure stability and measure performance improvements across different models and system versions.

---

## 1. Overview

The eval suite consists of YAML-based test cases that define a user query, expected tool calls, and content assertions.

```yaml
id: basic_math_001
tags: [bash, math]
difficulty: easy
category: reasoning
input:
  prompt: "What is 15 * 27?"
expected:
  tool_called: bash
  stdout_contains: "405"
optimization_set: true
holdout_set: false
timeout_seconds: 30.0
```

**Required fields:** `id`, `tags`, `input`, `expected`  
**Optional fields:** `difficulty`, `category`, `optimization_set`, `holdout_set`, `timeout_seconds`, `version`

---

## 2. Key Components

- **`vibe/evals/runner.py`**: Core execution engine for individual eval cases.
  - **Current behavior:** Reuses a single `QueryLoop` instance across cases; calls `clear_history()` between cases.
  - **Planned fix:** Factory-per-case isolation to prevent state bleed.
- **`vibe/evals/multi_model_runner.py`**: Runs the suite against multiple models and produces comparative `Scorecard` objects with per-tag breakdowns. Correctly creates a fresh `QueryLoop` per model.
- **`vibe/evals/soak_test.py`**: Stress tests over long durations (default 60 min) with degradation detection (compares first 20% vs last 20% latencies). Correctly creates a fresh `QueryLoop` per iteration.
- **`vibe/evals/observability.py`**: Collects spans, counters, gauges, and histograms during eval runs. Exports to JSON.

---

## 3. Running Evals

### End-to-End Suite
```bash
python run_e2e_evals.py
```

### Benchmarking Multiple Models
```bash
python run_e2e_evals.py benchmark --models gpt-4,claude-3-sonnet
```

### Soak Test
```bash
python run_e2e_evals.py soak --model primary-brain --duration 60
```

---

## 4. Assertion Types (11)

| Assertion | Description |
|-----------|-------------|
| `file_exists` | Path exists on disk |
| `file_contains` + `contains_text` | File contains substring |
| `stdout_contains` | Substring in tool output |
| `response_contains` | Substring(s) in LLM natural-language response |
| `response_contains_any` | At least one match in LLM response |
| `min_response_length` | Total response char count |
| `tool_called` | Specific tool was invoked |
| `tool_sequence` | Exact tool call order |
| `no_tool_called` | Zero tool invocations |
| `context_truncated` | CompactionCoordinator triggered |
| `metrics_threshold` | Latency/token budget checks |

---

## 5. Eval Store Schema

Results are persisted in SQLite (`~/.vibe/memory/evals.db`):

- **`evals`** table: Cases (id, tags, input, expected, optimization_set, holdout_set)
- **`eval_results`** table: Runs (eval_id, passed, diff, timestamp, total_tokens, latency_seconds)

**Note:** The `EvalResult` dataclass includes a `diff_score` field (0.0-1.0 for open-ended evals) that is not yet reflected in the database schema.

---

## 6. Built-in Cases

The built-in cases are located in `vibe/evals/builtin/` (~47 cases) and cover:
- File system operations (read/write/redirect)
- Complex bash commands and scripting
- Multi-step reasoning and error recovery
- Context compaction efficiency
- Security and jailing constraints
- Model fallback behavior
- Cancellation safety
- Observability metrics
- Memory wiki operations
- Query loop factory behavior

---

## 7. Validation

All eval YAML files are validated by `scripts/validate_eval_tags.py`:
- Required keys: `id`, `tags`, `input`, `expected`
- Valid `difficulty`: easy, medium, hard
- Valid `subsystem`: query_loop, tool_system, harness, etc.
- Valid `category`: file_ops, bash, reasoning, security, etc.
- Unique IDs across all files

---

*Last updated: 2026-04-25*
