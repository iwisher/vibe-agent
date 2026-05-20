# Vibe Agent — Agent Onboarding Guide

> This file is for AI coding agents. It assumes you know nothing about the project.
> Read this first before making any code changes.

---

## 1. Project Overview

**Vibe Agent** is an open, visual-first interactive CLI agent harness platform. It provides a resilient, secure, and model-agnostic environment for LLM-based autonomous tasks.

Key capabilities:
- **Multi-Provider Fallback**: Seamlessly switches between OpenAI, Anthropic, Kimi, OpenRouter, and Ollama via an adapter-based gateway with circuit breakers and latency-aware routing.
- **Secure Tool Execution**: 5-layer security defense (pattern scanning, file safety, human approval, smart approver, checkpoints) with sandboxed Bash and jailed File tools.
- **Context Management**: Automated compaction with 4 strategies (TRUNCATE, LLM_SUMMARIZE, OFFLOAD, DROP), plus adaptive iteration budgets.
- **Eval-Driven Development**: 50+ built-in eval cases, adversarial testing, multi-model scorecards, and soak tests with degradation detection.
- **Skill System v2**: Native skill format with TOML frontmatter, validation, security scanning, atomic installation, typed variables, orchestration, marketplace, and dynamic tool declaration.
- **Skill-Maker (Self-Improving)**: Auto-detects recurring task patterns from wiki extractions, generates SKILL.md drafts via LLM, validates through sandbox, and proposes installation.
- **Tripartite Memory System**: Automated async knowledge extraction, FlashLLM contradiction detection, telemetry-triggered RLM analysis, vector search with sentence-transformers, wiki graph database, and per-tag novelty thresholds.
- **Shadow Workspace Rollbacks**: Auto-creates hidden git branch (`vibe/shadow-<session-id>`) before write-heavy operations; one-command restore on failure.
- **Multi-Agent Swarm**: DAG-based orchestration of specialized sub-agents (Research, Coding, Critic, Planner) with Pub/Sub message bus and shared wiki.
- **React Trace Dashboard**: Web UI for session observability — timeline, wiki graph, telemetry charts, system stats. Dark theme, real-time WebSocket updates.
- **Preference Layer**: 8 persistent heuristics converting user feedback into agent behavior.

The project is currently at **Phase 4.2 (Self-Improving Skill-Maker) + Phase 5.2 (Shadow Workspace)**.

---

## 2. Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Package Manager | `pip` (editable install) or `uv` (lockfile: `uv.lock` present) |
| CLI Framework | Typer + Rich console |
| Validation | Pydantic v2 + pydantic-settings |
| HTTP Clients | aiohttp, httpx |
| Web Dashboard | FastAPI + Uvicorn (optional extra `[api]`) |
| Frontend | React 18 (CDN-loaded, no build step) + D3.js + Recharts |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`, 384-dim) with fastText fallback |
| Testing | pytest + pytest-asyncio |
| Linting / Formatting | ruff + black + mypy |
| CI/CD | GitHub Actions (`.github/workflows/ci.yml`) |

---

## 3. Project Structure

```
vibe-agent/
├── pyproject.toml              # Project config, deps, tool settings
├── uv.lock                     # uv lockfile (optional)
├── vibe/                       # Main source package
│   ├── __main__.py             # Entry point: `python -m vibe`
│   ├── cli/main.py             # Typer CLI root (`vibe` command)
│   ├── cli/skill_commands.py   # `vibe skill *` subcommands
│   ├── core/                   # Query loop, model gateway, config, coordinators
│   │   ├── query_loop.py       # ~1170-line state machine (IDLE → PLANNING → ... → COMPLETED)
│   │   ├── model_gateway.py    # Multi-provider LLM gateway with circuit breakers
│   │   ├── query_loop_factory.py  # Wires all components together
│   │   ├── config.py           # Hierarchical config (default → ~/.vibe/config.yaml → env)
│   │   └── coordinators.py     # ToolExecutor, FeedbackCoordinator, SecurityCoordinator, etc.
│   ├── adapters/               # LLM provider adapters (openai, anthropic, registry)
│   ├── harness/                # Harness-level components
│   │   ├── planner.py          # ContextPlanner (keyword + embedding + LLM routing)
│   │   ├── constraints.py      # Hook pipeline (PRE_VALIDATE → POST_FIX)
│   │   ├── feedback.py         # FeedbackEngine for quality gating
│   │   ├── instructions.py     # System prompt assembly
│   │   ├── embeddings.py       # Unified embedding loader (singleton + LRU cache)
│   │   ├── skills/             # Skill system v2
│   │   │   ├── parser.py       # TOML frontmatter + markdown parser
│   │   │   ├── models.py       # Pydantic Skill models
│   │   │   ├── validator.py    # Security scanning
│   │   │   ├── executor.py     # Step execution with variable substitution
│   │   │   ├── installer.py    # Atomic install from git/tarball/local
│   │   │   ├── maker.py        # Self-improving Skill-Maker pipeline
│   │   │   └── orchestrator.py # Inter-skill await + sub-agent spawn
│   │   └── memory/             # Harness memory layer
│   │       ├── trace_store.py  # Session persistence (SQLite/JSON/Memory)
│   │       ├── eval_store.py   # Eval result storage
│   │       ├── wiki.py         # LLMWiki markdown storage
│   │       └── session_store.py
│   ├── memory/                 # Tripartite memory system
│   │   ├── extraction.py       # Async knowledge extraction
│   │   ├── wiki.py             # Wiki page CRUD + FlashLLM contradiction
│   │   ├── pageindex.py        # Vector-based routing index
│   │   ├── wiki_graph.py       # Entity-relationship graph
│   │   ├── vector_index.py     # UpgradedVectorIndex (sentence-transformers)
│   │   ├── semantic_dedup.py   # Vector similarity deduplication
│   │   ├── novelty_thresholds.py
│   │   ├── rlm_analyzer.py     # Telemetry-driven LoRA trigger analysis
│   │   ├── rlm_trainer.py      # LoRA fine-tuning launcher
│   │   └── telemetry_collector.py
│   ├── tools/                  # Tool system + security
│   │   ├── bash.py             # Sandboxed Bash (subprocess_exec, no shell)
│   │   ├── file.py             # Jailed File operations
│   │   ├── tool_system.py      # Tool registry and execution
│   │   ├── git_shadow.py       # Shadow workspace manager
│   │   ├── mcp_bridge.py       # MCP protocol bridge
│   │   └── security/           # 5-layer security components
│   │       ├── patterns.py     # Dangerous command regex scanner
│   │       ├── file_safety.py  # Path traversal protection
│   │       ├── human_approval.py
│   │       ├── smart_approver.py
│   │       ├── checkpoints.py  # Rollback point manager
│   │       ├── redaction.py    # Secret stripping before persistence
│   │       └── audit.py        # Security event audit log
│   ├── swarm/                  # Multi-agent orchestration
│   │   ├── protocol.py         # Pub/Sub EventBroker + MessageBus
│   │   ├── agent.py            # SubAgent lifecycle + roles
│   │   ├── orchestrator.py     # TaskDAG scheduler
│   │   └── shared_wiki.py      # Coordinated wiki writes
│   ├── preferences/            # 8-domain preference layer
│   │   ├── registry.py         # SQLite WAL-backed persistence
│   │   ├── tool_prefs.py
│   │   ├── approval_rules.py
│   │   ├── style_policy.py
│   │   ├── macro_session.py    # YAML multi-step workflows
│   │   ├── recovery_rules.py
│   │   ├── compaction_policy.py
│   │   ├── provider_prefs.py
│   │   └── extraction_policy.py
│   ├── evals/                  # Evaluation infrastructure
│   │   ├── runner.py           # EvalRunner (core engine)
│   │   ├── factory_runner.py   # Fresh QueryLoop per case
│   │   ├── adversarial.py      # Prompt injection / jailbreak tests
│   │   ├── soak_test.py        # Long-running stress tests
│   │   ├── multi_model_runner.py
│   │   ├── observability.py    # OpenTelemetry-style spans/metrics
│   │   └── builtin/            # 47+ YAML eval case definitions
│   ├── dashboard/              # FastAPI backend + React frontend
│   │   ├── server.py           # FastAPI server (WebSocket, token auth)
│   │   ├── api.py              # REST endpoint definitions
│   │   └── data.py             # Async wrappers around stores
│   └── api/routes/             # Additional API route modules
├── tests/                      # 1400+ test functions across 120+ files
│   ├── adapters/
│   ├── cli/
│   ├── core/
│   ├── dashboard/
│   ├── evals/
│   ├── harness/
│   ├── memory/
│   ├── preferences/
│   ├── swarm/
│   ├── tools/security/
│   └── tools/
├── scripts/
│   ├── ci_eval_report.py       # CI regression check + markdown report
│   └── validate_eval_tags.py   # Eval YAML schema validator
├── docs/
│   ├── ARCHITECTURE.md         # Deep-dive system architecture
│   ├── CONFIGURATION.md        # Full config reference
│   ├── EVALUATION.md           # Eval suite docs
│   ├── ROADMAP.md              # Feature roadmap
│   ├── CHANGELOG.md
│   └── sample_config.yaml      # Example ~/.vibe/config.yaml
├── skills/                     # Local skill directory (e.g. stock-analysis/)
├── wiki/                       # Session wiki pages (markdown)
├── logs/                       # Session logs
└── archive/                    # Reference implementations from earlier phases
```

---

## 4. Build and Test Commands

### Install (Development)
```bash
# Editable install with all dev dependencies
pip install -e ".[dev]"

# Optional extras
pip install -e ".[api]"      # FastAPI dashboard server
pip install -e ".[memory]"   # sentence-transformers + torch
pip install -e ".[rlm]"      # PEFT + transformers + datasets (LoRA training)
```

### Run the Agent
```bash
# Interactive chat
python -m vibe

# One-shot query
python -m vibe "What is the 52-week high of QQQ?"

# With a specific model
python -m vibe --model qwen3:8b "Explain async/await in Python"

# With debug logging
python -m vibe --debug

# Dashboard
vibe dashboard start --port 8080 --no-auth   # dev mode
```

### Testing
```bash
# Run full test suite (pytest-asyncio auto mode enabled in pyproject.toml)
pytest -x --tb=short -q

# Run with coverage (project uses .coverage file)
pytest --cov=vibe --cov-report=term-missing

# Run a specific subsystem
pytest tests/memory/ -v
pytest tests/core/test_query_loop.py -v
```

### Linting and Formatting
```bash
# Format code
black vibe/ tests/

# Lint with ruff
ruff check vibe/ tests/
ruff format vibe/ tests/

# Type check
mypy vibe/
```

### Evaluations
```bash
# Run built-in eval suite (50+ cases)
vibe eval run

# Filter by subsystem tag
vibe eval run --tag subsystem=memory

# Run soak test
vibe eval soak --duration 30 --cpm 6

# Update performance baseline
vibe eval update-baseline

# CI eval suite (limited to 20 cases)
python -m vibe.cli.main eval run --limit 20
```

---

## 5. Code Style Guidelines

- **Line length**: 100 characters (`tool.black.line-length = 100`, `tool.ruff.line-length = 100`)
- **Target Python**: 3.11+ (`tool.black.target-version = ['py311']`)
- **Import style**: ruff-enforced (`select = ["E", "F", "I", "W"]`)
- **Type hints**: Use throughout; `mypy` runs in CI with `ignore_missing_imports = true`
- **Async-first**: The QueryLoop and most core components are `async`. Use `asyncio` patterns.
- **Pydantic v2**: All data models use Pydantic v2. Avoid v1 compatibility syntax.
- **Docstrings**: Use Google-style or descriptive docstrings for public APIs.
- **Constants**: Uppercase for module-level constants; private helpers prefixed with `_`.
- **Error handling**: Prefer explicit exception types over bare `except`. Use `ErrorRecovery` and `RetryPolicy` for transient failures.

---

## 6. Testing Strategy

- **Test count**: 1400+ test functions across 120+ files.
- **Framework**: pytest with `pytest-asyncio` in auto mode.
- **Structure**: Mirror the `vibe/` package under `tests/` (e.g. `vibe/core/config.py` → `tests/core/test_config.py`).
- **Test types**:
  - **Unit tests**: Per-module, heavy use of `unittest.mock.AsyncMock` and `MagicMock`.
  - **Integration tests**: Query loop end-to-end, memory system integration, tool security integration.
  - **Security tests**: Dedicated `tests/tools/security/` for each security layer.
  - **Eval tests**: Runner, adversarial, factory isolation, soak tests.
- **Factory-per-case**: For eval isolation, always create a fresh `QueryLoop` via `QueryLoopFactory` rather than reusing instances. This prevents state bleed.
- **CI**: GitHub Actions runs:
  1. `ruff check` + `ruff format --check`
  2. `pytest -x --tb=short -q` on Python 3.11, 3.12, 3.13
  3. Eval suite (limited) + regression check via `scripts/ci_eval_report.py`
- **Regression gate**: CI fails if eval score drops >5% below `docs/baseline_scorecard.json`.

---

## 7. Security Considerations

This project handles LLM tool execution, file system access, and API keys. Security is not an afterthought.

- **5-Layer Defense** (in order):
  1. **PatternEngine**: Regex denylist (`sudo`, `rm -rf /`, etc.) in `vibe/tools/security/patterns.py`
  2. **FileSafetyGuard**: `_resolve_and_jail()` prevents path traversal even via symlinks.
  3. **HumanApprover**: `manual` / `smart` / `auto` approval modes. Configurable via `security.approval_mode`.
  4. **SmartApprover**: LLM-based risk assessment for benign false positives.
  5. **CheckpointManager**: Creates rollback points before destructive ops.
- **Secret Redaction**: Automatic stripping of API keys and passwords from trace stores, logs, and eval stores. Never persist raw credentials.
- **Env Sanitization**: Blocks path overrides, strips shell env, blocks secret-prefix env vars.
- **Bash Sandbox**: Uses `subprocess_exec` (no shell) with timeout and regex denylist.
- **Fail Closed**: `security.fail_closed = true` — any security component failure defaults to deny.
- **Skill Validation**: `SkillValidator` scans for filesystem destruction, pipe-to-shell, eval injection, suspicious URLs, and hardcoded credentials before installation.
- **Shadow Workspace**: Git shadow branches provide a rollback safety net for write-heavy sessions.

**Agent rule**: Do not weaken security defaults, skip validation, or disable redaction in production code. Any security change must include tests in `tests/tools/security/`.

---

## 8. Configuration

The agent reads configuration from:
1. **Built-in defaults** (in code)
2. **`~/.vibe/config.yaml`** (user config)
3. **Environment variables** (`VIBE_*`)

Key config sections:
- `llm` / `providers` / `models` / `fallback` — Model gateway setup
- `security` — Approval mode, file safety, env sanitization, sandbox backend
- `compactor` — Token budget and compaction strategy
- `query_loop` — Iteration limits and feedback thresholds
- `memory` — Wiki, PageIndex, RLM analyzer settings
- `trace_store` — Session persistence backend (`sqlite`, `json`, or `memory`)
- `skill_maker` — Self-improving pipeline settings
- `shadow_workspace` — Git shadow branch settings

See `docs/sample_config.yaml` for a complete example and `docs/CONFIGURATION.md` for reference.

---

## 9. Key Conventions for Agents

### Before Making Changes
1. **Read the relevant module's docstring and comments** — the codebase is heavily commented.
2. **Check `docs/ARCHITECTURE.md`** for component relationships and design philosophy.
3. **Check `docs/ROADMAP.md`** to understand current phase priorities.
4. **Run existing tests** for the module you are touching: `pytest tests/<module>/ -v`

### When Adding Features
- **Mirror test structure**: If you add `vibe/new_module.py`, add `tests/test_new_module.py`.
- **Use Pydantic models** for all new data structures.
- **Prefer async**: If the feature interacts with the QueryLoop, ModelGateway, or tools, use `async`/`await`.
- **Add eval cases** if the feature changes agent behavior: create a YAML in `vibe/evals/builtin/`.
- **Update config schema** in `vibe/core/config.py` if new settings are needed.
- **Security review**: Any tool or skill change needs a security test.

### When Fixing Bugs
- **Add a regression test** that fails before the fix and passes after.
- **Check eval suite**: Run `vibe eval run` to ensure no regressions.
- **Minimal changes**: Prefer surgical fixes over broad refactors.

### File Modification Rules
- Do not modify files outside the working directory unless explicitly instructed.
- Do not commit or push git changes unless explicitly asked.
- Do not run destructive bash commands without understanding the denylist patterns.

---

## 10. Useful References

| Document | Purpose |
|----------|---------|
| `README.md` | Quick start, feature overview, CLI examples |
| `docs/ARCHITECTURE.md` | System philosophy, component deep-dives, state machine flow |
| `docs/CONFIGURATION.md` | Full config reference with environment variable overrides |
| `docs/EVALUATION.md` | Eval case YAML schema, assertion types, running instructions |
| `docs/ROADMAP.md` | Feature roadmap and current phase priorities |
| `docs/CHANGELOG.md` | Version history |
| `pyproject.toml` | Dependencies, build config, pytest/black/ruff/mypy settings |
| `scripts/ci_eval_report.py` | CI regression gate logic |
| `scripts/validate_eval_tags.py` | Eval YAML validator for CI |
