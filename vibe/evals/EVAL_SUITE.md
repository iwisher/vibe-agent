# Vibe-Agent Eval Suite

## Overview

This directory contains the end-to-end eval cases for vibe-agent.

| Metric | Count |
|--------|-------|
| Total cases | 32 |
| Categories | bash, file_ops, multi_step, reasoning, edge, error_recovery, instruction_following, tool_selection, security, complex, finance, meta |
| Subsystems covered | query_loop, error_recovery, constraints, planner, compactor |

## Case Format

```yaml
id: unique-case-id
tags: [category, subsystem=query_loop, difficulty=easy]
input:
  prompt: "The user prompt"
  preconditions: []
expected:
  stdout_contains: "expected text"
  response_contains: "expected text"
  file_exists: /tmp/path
  file_contains: /tmp/path
  contains_text: "text in file"
  tool_called: "tool_name"
  no_tool_called: true
  context_truncated: true
optimization_set: true
holdout_set: false
```

## Assertion Types

| Assertion | Description |
|-----------|-------------|
| `stdout_contains` | Text found in tool stdout/stderr output |
| `response_contains` | Text found in LLM natural language response (supports list, all must match) |
| `response_contains_any` | At least one of the listed strings found in LLM response |
| `min_response_length` | Minimum total character length of LLM responses |
| `file_exists` | File exists at path (supports `~` expansion) |
| `file_contains` + `contains_text` | File contains specific text (supports `~` expansion) |
| `tool_called` | Specific tool was invoked |
| `no_tool_called` | No tools were invoked |
| `context_truncated` | Context compactor triggered |

## Cases by Category

### Bash (10 cases)
- `bash-math-001` — Basic math via bash
- `bash-stats-001` — File creation + line counting
- `bash-echo-001` — Simple echo
- `date-bash-001` — Date command
- `math-bash-002` — Addition
- `uppercase-bash-001` — Text transformation
- `word-count-bash-001` — Word counting
- `grep-bash-001` — Pattern matching
- `mkdir-bash-001` — Directory creation
- `simple-list-001` — File listing

### File Ops (5 cases)
- `file-read-001` — Read existing file
- `file-edit-001` — Overwrite file
- `file-create-read-001` — Create and read
- `file-overwrite-001` — Sequential overwrite
- `tool-selection-001` — Conditional file creation

### Multi-step (2 cases)
- `multi-step-001` — Write + read + append
- `multi-step-002` — Write + read + write different file

### Reasoning (1 case)
- `instruction-following-001` — Exact content instructions

### Math (2 cases)
- `bash-math-001` — Basic math via bash
- `basic-math-002` — Simple multiplication

### Edge (2 cases)
- `edge-empty-input-001` — Empty prompt handling
- `edge-unicode-001` — Unicode handling

### Error Recovery (2 cases)
- `error-recovery-001` — Retry after failure
- `error-recovery-002` — Nonexistent command

### Simple Bash (1 case)
- `simple-echo-001` — Basic echo command

### Security (1 case)
- `security-hook-001` — Sudo blocking

### Complex (5 cases)
- `complex-multi-file-001` — Write 2 files → read → cross compute
- `complex-conditional-001` — Check file existence → conditionally create
- `complex-tool-chain-001` — 5+ step tool chain (create/list/filter/count/write)
- `complex-recovery-001` — Deliberate error → recovery → verify
- `complex-mixed-001` — Bash generate data → file write → bash process → read verify

### Finance (1 case)
- `us-stock-weekly-001` — Open-ended US stock market summary + next-week directional outlook with structured fact-check assertions (direction, length, keyword coverage)

### Meta (1 case)
- `skill-install-001` — Agent uses `skill_manage` tool to create a real skill file on disk and reports the path

## Known Limitations

1. **Model non-determinism**: Some cases (especially bash-math, security-hook) may fail intermittently due to model choosing not to call tools for simple tasks.
2. **API flakiness**: The LLM endpoint may return 503 errors, affecting pass rates.
3. **Constraints cases removed**: Permission gate and policy violation cases require model to attempt blocked actions, which modern LLMs often refuse. Revisit when hook-layer testing is decoupled from LLM behavior.

## Adding New Cases

1. Create a YAML file in `vibe/evals/builtin/`
2. Include `subsystem`, `difficulty`, and `category` tags
3. Use `optimization_set: true` for training/evaluation cases
4. Use `holdout_set: true` for final validation only
5. Run `python run_e2e_evals.py eval` to verify
