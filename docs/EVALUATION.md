# Evaluation Suite

Vibe Agent includes a comprehensive evaluation infrastructure to ensure stability and measure performance improvements across different models and system versions.

## 1. Overview

The eval suite consists of YAML-based test cases that define a user query, expected tool calls, and content assertions.

```yaml
name: basic_math_001
description: Verify the agent can perform basic math via bash
query: "What is 15 * 27?"
assertions:
  - type: tool_called
    tool: bash
  - type: stdout_contains
    content: "405"
```

## 2. Key Components

- **`vibe/evals/runner.py`**: The core execution engine for individual eval cases.
- **`vibe/evals/multi_model_runner.py`**: Runs the entire suite against multiple models to compare performance.
- **`vibe/evals/soak_test.py`**: Stress tests the agent over long durations to find edge cases or resource leaks.
- **`vibe/evals/observability.py`**: Collects traces and metrics during eval runs.

## 3. Running Evals

### End-to-End Suite
To run the full built-in suite using your default model:
```bash
python run_e2e_evals.py
```

### Benchmarking Models
To compare multiple models:
```bash
python run_e2e_evals.py benchmark --models gpt-4,claude-3-sonnet
```

## 4. Assertion Types

- `tool_called`: Verify a specific tool was used.
- `stdout_contains`: Check if the tool's output contains a string.
- `content_contains`: Check if the agent's final response contains a string.
- `tool_sequence`: Verify the exact order of multiple tool calls.
- `metrics_threshold`: Ensure latency or token usage stays within bounds.

## 5. Built-in Cases

The built-in cases are located in `vibe/evals/builtin/` and cover:
- File system operations (read/write/redirect).
- Complex bash commands and scripting.
- Multi-step reasoning and error recovery.
- Context compaction efficiency.
- Security and jailing constraints.
