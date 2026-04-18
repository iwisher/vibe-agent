# Vibe Agent Eval Suite

This document defines the eval case format, assertion types, tag schema, and CI validation rules for the Vibe Agent harness.

## 1. Eval Case Format

Each eval case is a YAML file in `vibe/evals/builtin/`. Required top-level keys:

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `id` | string | ✓ | Unique kebab-case identifier |
| `tags` | list[str] | ✓ | Descriptive tags (see Tag Schema) |
| `subsystem` | string | ✓ | Component under test |
| `difficulty` | string | ✓ | `easy` | `medium` | `hard` |
| `category` | string | ✓ | Functional bucket |
| `input` | dict | ✓ | Eval input (e.g. `prompt`) |
| `expected` | dict | ✓ | Assertion map |
| `optimization_set` | bool | × | Include in hill-climbing (default `true`) |
| `holdout_set` | bool | × | Exclude from training (default `false`) |

### Example

```yaml
id: bash-echo-001
tags: [bash, basic]
subsystem: query_loop
difficulty: easy
category: bash
input:
  prompt: "Echo hello world"
expected:
  stdout_contains: "hello world"
  tool_called: "bash_tool"
```

## 2. Assertion Types

| Assertion | Expected Value | Description |
|-----------|---------------|-------------|
| `file_exists` | path string | File was created |
| `file_contains` | path string | Pair with `contains_text` |
| `contains_text` | string | Substring found in `file_contains` target |
| `stdout_contains` | string | Found in any tool result content/error |
| `tool_called` | string | Named tool appears in assistant tool_calls |
| `tool_sequence` | list[str] | Exact ordered tool call sequence |
| `no_tool_called` | bool | No tools were invoked |
| `context_truncated` | bool | Context compaction occurred |
| `response_contains` | str \| list[str] | Text found in LLM response(s) |
| `response_contains_any` | list[str] | At least one text found |
| `min_response_length` | int | Total response char count |
| `metrics_threshold` | dict | `{max_latency_seconds: float, max_total_tokens: int}` |

## 3. Tag Schema

### Required Tags (auto-injected if missing)

| Tag Key | Valid Values |
|---------|-------------|
| `subsystem` | `query_loop`, `planner`, `compactor`, `feedback`, `mcp_bridge`, `error_recovery`, `tool_system` |
| `difficulty` | `easy`, `medium`, `hard` |
| `category` | `bash`, `file_ops`, `math`, `reasoning`, `multi_step`, `edge`, `error_recovery`, `tool_use`, `meta`, `general` |

### Freestyle Tags

Any additional descriptive tags are allowed (e.g. `complex`, `robustness`, `skill`).

## 4. Eval Categories

### By Subsystem

| Subsystem | Count | Description |
|-----------|-------|-------------|
| `query_loop` | ~24 | End-to-end tool-use and reasoning |
| `error_recovery` | ~3 | Retry, fallback, graceful failure |
| `planner` | 0* | Context planner selection (unit-tested) |
| `mcp_bridge` | 0* | MCP discovery/execution (unit-tested) |
| `compactor` | 0* | Context compaction (unit-tested) |
| `feedback` | 0* | Feedback retry logic (unit-tested) |

\* These subsystems are covered by Python unit tests rather than YAML eval cases.

### By Difficulty

- **easy**: Single tool call, deterministic output
- **medium**: 2-3 steps, conditional logic
- **hard**: Multi-step chains, error recovery, complex reasoning

## 5. CI Validation

Run the validation script before every PR:

```bash
python scripts/validate_eval_tags.py
```

Rules enforced:
1. Every YAML file has required keys (`id`, `tags`, `subsystem`, `difficulty`, `category`, `input`, `expected`)
2. `difficulty` ∈ {easy, medium, hard}
3. `subsystem` and `category` are non-empty
4. `id` is unique across all files

Failure returns exit code 1 and prints a report.

## 6. Phase 2 Additions

This section documents evals added during Phase 2 (eval suite expansion):

| Eval ID | Subsystem | Assertion | Purpose |
|---------|-----------|-----------|---------|
| planner_001 | planner | unit test | Correct tool selection |
| planner_002 | planner | unit test | No over-select on irrelevant query |
| planner_003 | planner | unit test | Skill matching accuracy |
| mcp_001 | mcp_bridge | unit test | Discovery returns schemas |
| mcp_002 | mcp_bridge | unit test | Execute tool successfully |
| mcp_003 | mcp_bridge | unit test | Invalid tool graceful error |
| compactor_001 | compactor | unit test | Trigger at token threshold |
| compactor_002 | compactor | unit test | Preserve key info after compaction |
| feedback_001 | feedback | unit test | Low score triggers retry |
| feedback_002 | feedback | unit test | High score skips retry |
| edge_001 | query_loop | unit test | Empty tool list doesn't crash |
| edge_002 | query_loop | unit test | Malformed args graceful error |
| edge_003 | query_loop | unit test | Max iteration exhaustion |
| assert_tool_sequence | runner | EvalRunner | Ordered tool call sequence |
| assert_metrics_threshold | runner | EvalRunner | Latency/token budget check |
