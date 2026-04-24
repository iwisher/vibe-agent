# Vibe Agent — Project Wiki

> **Version**: 0.2.0-alpha  
> **Phase**: Core Harness Hardening (Phase 1)  
> **Last Updated**: 2026-04-23

---

## Table of Contents

1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Architecture](#architecture)
4. [Core Components](#core-components)
5. [Tool System](#tool-system)
6. [Configuration](#configuration)
7. [CLI & API](#cli--api)
8. [Evaluation Suite](#evaluation-suite)
9. [Testing](#testing)
10. [Roadmap](#roadmap)
11. [Changelog](#changelog)
12. [Security](#security)
13. [Development Guide](#development-guide)

---

## Overview

**Vibe Agent** is an open, visual-first interactive CLI agent harness. It provides a robust, resilient, and secure environment for LLM-based autonomous tasks, designed to be **independent of any specific model or provider**.

### Key Capabilities

| Capability | Description |
|------------|-------------|
| **Multi-Provider Fallback** | Seamlessly switches between OpenAI, Anthropic, OpenRouter, Ollama, and other providers when primary models fail. |
| **Secure Tool Execution** | Sandboxed Bash and File system tools with three-layer security defense and path jailing. |
| **Context Management** | Automated compaction and summarization to handle long-running conversations within token limits. |
| **Eval-Driven Development** | Built-in suite of 30+ evaluation cases to ensure every update maintains performance and stability. |
| **Customizable Skills** | Extend the agent's capabilities with markdown-based skill definitions. |

### System Philosophy

1. **Model Agnosticism** — Hop between models and providers seamlessly.
2. **Zero-Trust Tools** — All tools are "jailed" with multi-layer validation.
3. **Stability over Speed** — Circuit breakers and exponential backoff ensure stability.
4. **Empirical Progress** — Every change validated against the `vibe eval` suite.

---

## Project Structure

```
vibe-agent/
├── vibe/                       # Main source package
│   ├── adapters/               # LLM API adapters (OpenAI, Anthropic)
│   ├── api/                    # FastAPI routes (future REST/WebSocket server)
│   ├── cli/                    # Typer-based CLI entry point
│   ├── core/                   # Core harness engine
│   ├── evals/                  # Evaluation infrastructure
│   ├── harness/                # Orchestration, constraints, memory
│   └── tools/                  # Tool system
├── tests/                      # Comprehensive test suite (~40 test files)
├── docs/                       # Documentation
├── scripts/                    # Utility scripts
├── archive/                    # Reference implementations (inactive)
├── run_e2e_evals.py            # Standalone end-to-end eval runner
├── pyproject.toml              # Project configuration
├── README.md                   # Project README
├── CHANGELOG.md                # Version history
└── WIKI.md                     # This file
```

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `vibe/adapters/` | `OpenAIAdapter`, `AnthropicAdapter`, adapter registry |
| `vibe/core/` | QueryLoop, ModelGateway, coordinators, config, error recovery |
| `vibe/evals/` | EvalRunner, benchmarking, soak tests, observability, model registry |
| `vibe/harness/` | Constraints, feedback, planner, instructions, memory, orchestration |
| `vibe/tools/` | ToolSystem, BashTool, FileTool, MCP bridge, skill management |
| `tests/` | ~40 test files covering all major components |
| `docs/` | Architecture, configuration, evaluation, roadmap, reviews |
| `archive/` | Preserved reference implementations from earlier phases |

---

## Architecture

### High-Level Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface (CLI)                 │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                      Query Loop                         │
│   (State Machine, Context Compactor, Hook Pipeline)     │
└──────────────┬─────────────┬─────────────┬──────────────┘
               │             │             │
               ▼             ▼             ▼
┌───────────────────┐ ┌─────────────┐ ┌───────────────────┐
│   Model Gateway   │ │ Tool System │ │ Instruction Set   │
│ (Multi-Provider,  │ │ (Bash, File,│ │ (Skills, AGENTS)  │
│  Fallback, CB)    │ │  MCP Bridge)│ │                   │
└───────────────────┘ └─────────────┘ └───────────────────┘
```

### Query Loop State Machine

| State | Description |
|-------|-------------|
| `IDLE` | Ready for new query |
| `PLANNING` | `ContextPlanner` selects relevant tools/skills/MCPs |
| `PROCESSING` | `CompactionCoordinator` checks token budget |
| `TOOL_EXECUTION` | `ToolExecutor` runs hooks + tools (with MCP fallback) |
| `SYNTHESIZING` | `FeedbackCoordinator` scores content; loops back if below threshold |
| `COMPLETED` | Successful termination |
| `INCOMPLETE` | Max iterations reached |
| `ERROR` | Unrecoverable error |
| `STOPPED` | User-cancelled or forced stop |

### Token Efficiency Design

| Layer | Component | Purpose |
|-------|-----------|---------|
| **Pre-filtering** | `ContextPlanner` | Selects only relevant tools/skills per query, reducing system prompt bloat. |
| **Compaction** | `CompactionCoordinator` | Monitors token usage; applies `SummarizeStrategy` (TRUNCATE, LLM_SUMMARIZE, OFFLOAD, DROP). |
| **Message Jailing** | `ContextCompactor` | Caps individual tool results at `max_chars_per_msg` to prevent noise overflow. |
| **Quality Gate** | `FeedbackCoordinator` | Self-verification scoring prevents hallucination loops and wasted tool calls. |

---

## Core Components

### Query Loop (`vibe/core/query_loop.py`)

The main orchestrator. Manages the agent's "thought-action" cycle as an async generator through states: IDLE → PLANNING → PROCESSING → LLM Call → (TOOL_EXECUTION or SYNTHESIZING) → COMPLETED/INCOMPLETE/ERROR/STOPPED.

### Model Gateway (`vibe/core/model_gateway.py`)

The resilience layer for all LLM communication.

| Feature | Implementation |
|---------|----------------|
| **Adapters** | `OpenAIAdapter`, `AnthropicAdapter` |
| **Registry-Aware Resolution** | Dynamically resolves `base_url`, `api_key`, `adapter` per fallback attempt |
| **Circuit Breaker** | Opens after 5 consecutive failures; 60s cooldown |
| **Retry Policy** | Exponential backoff with jitter |
| **Structured Output** | Coerces LLM responses to requested formats |

### Coordinators (`vibe/core/coordinators.py`)

Decomposed from the monolithic QueryLoop in v0.2.0:

1. **`ToolExecutor`** — Tool execution, `HookPipeline` integration, MCP fallback.
2. **`FeedbackCoordinator`** — Self-verification scoring (0.0–1.0), retry hints.
3. **`CompactionCoordinator`** — Token budget triggers for `ContextCompactor`.

### Context Compactor (`vibe/core/context_compactor.py`)

Manages long-running conversations within token limits.

- **Strategies**: `TRUNCATE`, `LLM_SUMMARIZE`, `OFFLOAD`, `DROP`
- **Estimator**: tiktoken when available; char-based fallback (`chars_per_token`)
- **Preservation**: Always keeps `preserve_recent` most recent messages

### Error Recovery (`vibe/core/error_recovery.py`)

- Exponential backoff with configurable jitter
- Actionable error hints per `ErrorType`
- Configurable retry policies (max retries, initial delay)

### Provider Registry (`vibe/core/provider_registry.py`)

```
ProviderProfile:
  - name: str
  - base_url: str
  - adapter: str        # "openai" | "anthropic"
  - api_key_env_var: str
  - extra_headers: dict[str, str]
```

### Configuration (`vibe/core/config.py`)

Hierarchical loading: **Defaults** → `~/.vibe/config.yaml` → **Environment Variables** (`VIBE_*`)

Environment variable overrides:
- `VIBE_MODEL` — default model
- `VIBE_BASE_URL` — fallback base URL
- `VIBE_FALLBACK_ENABLED` — enable/disable fallback

---

## Tool System

### Tool Registry (`vibe/tools/tool_system.py`)

- `Tool` ABC: `get_schema()` → OpenAI-style function schema; `execute()` → `ToolResult`
- `ToolSystem`: central registry for all tools

### Bash Tool (`vibe/tools/bash.py`)

**Three-Layer Security Defense:**

| Layer | Defense |
|-------|---------|
| 1 | `subprocess_exec` — no shell interpretation |
| 2 | Unquoted shell metacharacter rejection |
| 3 | Regex denylist: `sudo`, `curl\|bash`, fork bombs, `rm -rf /`, `eval`, etc. |

Additional features:
- **Allowlist mode**: `allowed_commands` restricts to specific commands
- **Timeout**: Process-group killing on expiry
- **Working directory**: Configurable cwd

### File Tool (`vibe/tools/file.py`)

**Path Jailing** via `_resolve_and_jail()`:
1. `Path.resolve()` to resolve symlinks
2. `relative_to()` check against working directory

Limits:
- Read: 10 MB max
- Write: 5 MB max

### MCP Bridge (`vibe/tools/mcp_bridge.py`)

- Bridge to Model Context Protocol servers for external tool integration
- Connection pooling support

### Skill Management (`vibe/tools/skill_manage.py`)

- Creates/manages markdown-based skills in `~/.vibe/skills/`
- Skills support YAML frontmatter

---

## Configuration

### File Location

`~/.vibe/config.yaml`

### Full Example

```yaml
llm:
  default_model: "primary-brain"
  base_url: "http://localhost:11434"
  timeout: 120.0

providers:
  openrouter:
    base_url: "https://openrouter.ai/api/v1"
    adapter: "openai"
    api_key_env_var: "OPENROUTER_API_KEY"
    extra_headers:
      "HTTP-Referer": "https://github.com/vibe-agent"
      "X-Title": "Vibe Agent"
  
  anthropic:
    base_url: "https://api.anthropic.com"
    adapter: "anthropic"
    api_key_env_var: "ANTHROPIC_API_KEY"

models:
  primary-brain:
    provider: "openrouter"
    model_id: "google/gemini-2.0-flash-001"
  
  reliable-fallback:
    provider: "anthropic"
    model_id: "claude-3-5-sonnet-latest"

fallback:
  enabled: true
  chain:
    - "primary-brain"
    - "reliable-fallback"
  max_retries: 2
  health_check_timeout: 10.0

compactor:
  max_tokens: 8000
  chars_per_token: 4.0
  preserve_recent: 4
  max_chars_per_msg: 4000

query_loop:
  feedback_threshold: 0.7
  max_feedback_retries: 1
  max_iterations: 50

retry:
  max_retries: 2
  initial_delay: 1.0

logging:
  session_dir: "~/.vibe/logs"
  retention_days: 30
  max_file_size_mb: 10
```

See `docs/CONFIGURATION.md` and `docs/sample_config.yaml` for complete details.

---

## CLI & API

### CLI Entry Point

```bash
# Install
pip install -e .

# Interactive session
python -m vibe
# or
vibe

# Single query
vibe "What is 15 * 27?"

# Run evals
vibe eval run --tag file_ops --limit 10

# Soak test
vibe eval soak --duration 60 --cpm 6

# Inspect memory
vibe memory traces --limit 20
```

### Standalone Eval Runner

```bash
# Run eval suite
python run_e2e_evals.py eval

# Multi-model benchmark
python run_e2e_evals.py benchmark --models gpt-4,claude-3-sonnet --parallel

# Soak test
python run_e2e_evals.py soak --duration 60 --cpm 6
```

### API (Future)

The `vibe/api/` package is scaffolded for Phase 2. FastAPI is an optional dependency (`pip install vibe-agent[api]`).

---

## Evaluation Suite

### Infrastructure

| Component | File | Purpose |
|-----------|------|---------|
| `EvalRunner` | `vibe/evals/runner.py` | Executes eval cases through QueryLoop |
| `MultiModelRunner` | `vibe/evals/multi_model_runner.py` | Benchmarks multiple models |
| `SoakTestRunner` | `vibe/evals/soak_test.py` | Long-running stress tests |
| `Observability` | `vibe/evals/observability.py` | Traces, metrics, spans, histograms |
| `ModelRegistry` | `vibe/evals/model_registry.py` | Semantic name → provider resolution |

### Built-in Eval Cases (`vibe/evals/builtin/`)

30+ YAML cases covering:

| Category | Cases |
|----------|-------|
| **File Operations** | create, read, write, edit, overwrite, traversal security |
| **Bash Execution** | math, echo, grep, stats, date, word count, uppercase, mkdir |
| **Security** | hooks, bash denylist, file jailing, TOCTOU, skill management |
| **Stability** | error recovery, timeout handling, cancellation safety |
| **Context Management** | compaction, planner keyword matching |
| **Multi-step Reasoning** | tool chains, sequential operations |
| **Observability** | metrics, trace validation |
| **Model Fallback** | cross-provider fallback chains |

### Eval Tags

Every eval case must specify:
- `subsystem=` — component under test
- `difficulty=` — easy / medium / hard
- `category=` — functional grouping

Validated by `scripts/validate_eval_tags.py`.

### Scorecards

- `docs/baseline_scorecard.json` — baseline performance metrics
- Generated per model run in JSON format

---

## Testing

### Test Suite (~40 files)

| Category | Files |
|----------|-------|
| **Adapters** | `test_openai_adapter.py`, `test_anthropic_adapter.py`, `test_registry.py` |
| **Core** | `test_model_gateway.py`, `test_query_loop.py`, `test_query_loop_edge.py`, `test_query_loop_factory_adapter.py`, `test_context_compactor.py`, `test_context_compactor_llm.py`, `test_error_recovery.py`, `test_circuit_breaker.py`, `test_feedback.py`, `test_planner.py` |
| **Config** | `test_config.py`, `test_config_providers.py`, `test_provider_registry.py` |
| **Tools** | `test_tool_system.py`, `test_bash_security.py`, `test_security_phase1.py`, `test_stability_phase2.py`, `test_mcp_bridge.py`, `test_mcp_bridge_pooling.py`, `test_mcps.py` |
| **Evals** | `test_eval_runner.py`, `test_eval_runner_assertions.py`, `test_eval_store.py`, `test_multi_provider_benchmark.py`, `test_soak_test.py`, `test_validate_eval_tags.py` |
| **Harness** | `test_instructions.py`, `test_sync_delegate.py`, `test_trace_store.py`, `test_observability.py`, `test_resource_leaks.py`, `test_health_check.py`, `test_health_check_providers.py` |
| **Integration** | `test_cli.py`, `test_imports.py`, `test_fallback.py` |

### Running Tests

```bash
# All tests
pytest

# Specific category
pytest tests/test_bash_security.py -v

# With coverage
pytest --cov=vibe --cov-report=html
```

---

## Roadmap

### Completed (v0.1.0 – v0.2.0)

- [x] Project scaffold and core directory structure
- [x] Model Gateway with multi-provider support
- [x] Cross-provider fallback with circuit breakers
- [x] Tool System (Bash, File, MCP Bridge)
- [x] Query Loop state machine
- [x] Context Compaction with summarization
- [x] Error Recovery with exponential backoff
- [x] Security hardening (Bash exec, File jailing)
- [x] 30+ built-in eval cases
- [x] Multi-model benchmarking infrastructure

### In Progress (Phase 1.x)

- [ ] Observability Hardening — end-to-end trace validation
- [ ] Wiki Memory — markdown-based long-term memory
- [ ] QueryLoop Cancellation Safety — robust cleanup
- [ ] Advanced Token Estimation — tiktoken integration

### Future (Phase 2+)

- [ ] Async Session Orchestration — long-lived, steerable sessions
- [ ] Topology Planner — sync vs. async execution logic
- [ ] FastAPI Server — REST and WebSocket API
- [ ] React Dashboard — visual trace viewing and monitoring
- [ ] Auto-Harness Optimizer — autonomous performance improvement
- [ ] MCP Bridge Expansion — enhanced MCP server support
- [ ] Cost-Aware Routing — cheapest capable model selection

---

## Changelog

### [0.2.0-alpha] — 2026-04-19

**Added:**
- Multi-Provider Support via `ProviderRegistry` and `ModelRegistry`
- Provider Adapters: `OpenAIAdapter`, `AnthropicAdapter`
- Cross-Provider Fallback with dynamic connection resolution
- Circuit Breaker (5 failures → 60s cooldown)
- Custom `extra_headers` at provider level
- Comprehensive documentation suite

**Fixed:**
- BashTool hardened: `subprocess_shell` → `subprocess_exec`
- FileTool path jailing hardened against symlinks
- `httpx.AsyncClient` resource leaks fixed
- Added explicit `INCOMPLETE` state for iteration exhaustion

**Changed:**
- Monolithic `QueryLoop` → `ToolExecutor`, `FeedbackCoordinator`, `CompactionCoordinator`
- Standardized `VibeConfig` parsing
- Unified typing styles across core package

### [0.1.0-alpha] — 2026-04-15

**Added:**
- Initial project scaffold (Python 3.11+, pytest, pyproject.toml)
- `model_gateway.py` — OpenAI-compatible client with retry
- `error_recovery.py` — exponential backoff
- `query_loop.py` — conversation loop with tool calls
- `context_compactor.py` — token-aware context management
- `tool_system.py` — tool registry
- `bash.py` / `file.py` — secure tool implementations
- `trace_store.py` / `eval_store.py` — SQLite storage
- `sync_delegate.py` — parallel subagent runner (3 workers)
- `main.py` — Typer CLI
- 3 built-in evals

**Security:**
- Removed hardcoded API key fallbacks
- Hardened BashTool with regex denylist + allowlist mode

---

## Security

### Defense Layers

| Layer | Component | Defense |
|-------|-----------|---------|
| **Bash Layer 1** | `BashTool` | `subprocess_exec` — no shell interpretation |
| **Bash Layer 2** | `BashTool` | Unquoted metacharacter rejection |
| **Bash Layer 3** | `BashTool` | Regex denylist (sudo, curl\|bash, eval, fork bombs, rm -rf /) |
| **File Layer 1** | `FileTool` | `Path.resolve()` to resolve symlinks |
| **File Layer 2** | `FileTool` | `relative_to()` jail check |
| **Hook Pipeline** | `constraints.py` | 5-stage governance: PRE_VALIDATE → PRE_MODIFY → PRE_ALLOW → POST_EXECUTE → POST_FIX |

### Security History

Two major independent reviews identified and fixed:
- Path traversal vulnerabilities
- TOCTOU (time-of-check/time-of-use) issues
- AsyncMock misuses in tests
- Cancellation safety gaps
- Hardcoded vendor URLs

### API Key Handling

- No hardcoded keys anywhere in codebase
- Keys read from environment variables (configurable per provider)
- `api_key_env_var` in provider config defines which env var to read

---

## Development Guide

### Setup

```bash
# Clone and install in editable mode
pip install -e ".[dev]"

# Install with API dependencies
pip install -e ".[api]"

# Install all extras
pip install -e ".[dev,api]"
```

### Code Quality

| Tool | Config |
|------|--------|
| **Black** | Line length 100, Python 3.11 target |
| **Ruff** | Enabled in pyproject.toml |
| **pytest** | `asyncio_mode = auto` |

### Key Commands

```bash
# Format code
black vibe/ tests/

# Lint
ruff check vibe/ tests/

# Run tests
pytest

# Run eval suite
python run_e2e_evals.py eval

# Run specific eval tag
vibe eval run --tag file_ops

# Soak test
vibe eval soak --duration 60 --cpm 6
```

### Project Conventions

- **Python**: 3.11+ required
- **Async-first**: All I/O is async (`asyncio`, `aiohttp`, `httpx`)
- **Type hints**: Full typing across core modules
- **Pydantic**: Used for configuration and data validation
- **Rich**: Console output formatting
- **Typer**: CLI framework

### Contributing

1. Every change must pass the eval suite: `python run_e2e_evals.py eval`
2. Add tests for new features
3. Update `CHANGELOG.md` for user-visible changes
4. Follow existing code style (Black, Ruff)

---

## Links & References

| Document | Path | Description |
|----------|------|-------------|
| README | `README.md` | Quick start and overview |
| Architecture | `docs/ARCHITECTURE.md` | Deep architectural dive |
| Configuration | `docs/CONFIGURATION.md` | Full configuration guide |
| Evaluation | `docs/EVALUATION.md` | Eval suite documentation |
| Roadmap | `docs/ROADMAP.md` | Project plans and milestones |
| Reviews | `docs/REVIEWS.md` | Code review archive |
| Changelog | `CHANGELOG.md` | Version history |
| Sample Config | `docs/sample_config.yaml` | Ready-to-use config example |

---

*This wiki is auto-generated and maintained alongside the codebase. For the latest information, refer to the source files and inline documentation.*
