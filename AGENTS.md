# Vibe Agent — Agent Onboarding Guide

> This file is for AI coding agents. It assumes you know nothing about the project.
> Read this first before making any code changes.

---

## 1. Project Overview

**Vibe Agent** is an open, visual-first interactive CLI agent harness platform. It provides a resilient, secure, and model-agnostic environment for LLM-based autonomous tasks.

Key capabilities:
- **Multi-Provider Fallback**: Seamlessly switches between OpenAI, Anthropic, Google Gemini (native protocol), Kimi, OpenRouter, and Ollama via an adapter-based gateway with circuit breakers, latency-aware routing, and cost tracking.
- **Streaming Responses**: Real-time token streaming (`--stream`) with native reasoning/thinking token display.
- **Interactive CLI + TUI**: Readline-based chat with persistent history, plus a `prompt_toolkit`-based TUI (`vibe/cli/tui.py`).
- **Secure Tool Execution**: 5-layer security defense (pattern scanning, file safety, human approval, smart approver, checkpoints) with sandboxed Bash, jailed File tools, and an SSRF-protected dual-tier Browser tool (static HTTP extraction, optional Playwright rendering).
- **Context Management**: Automated compaction with 4 strategies (TRUNCATE, LLM_SUMMARIZE, OFFLOAD, DROP), plus adaptive iteration budgets and a DAG-based task planner for parallel sub-tasks.
- **Session Durability**: `SessionRecoveryManager` with TTL-based checkpoints serializes QueryLoop state to SQLite for suspend/resume (`vibe session resume`).
- **Eval-Driven Development**: 50 built-in YAML eval cases, adversarial testing, multi-model scorecards, and soak tests with degradation detection.
- **Skill System v2**: Native skill format with TOML frontmatter, validation, security scanning, atomic installation, typed variables, orchestration, marketplace, and dynamic tool declaration. Deterministic script steps run bundled `scripts/` through the sandboxed Bash tool.
- **Skill-Maker (Self-Improving)**: Auto-detects recurring task patterns from wiki extractions, generates SKILL.md drafts via LLM, validates through sandbox, and proposes installation.
- **Tripartite Memory System**: Enabled by default. Automated async knowledge extraction (including failed sessions), post-session **trajectory reflection** (generality-gated lesson pages with usage-driven helpful/harmful counters), **lesson compaction** (`vibe memory wiki compact`), query-time injection on all planner tiers (confidence-gated, contradiction-aware), FlashLLM contradiction detection, telemetry-triggered RLM analysis (LoRA fine-tuning, AgentHER-style failure relabeling), vector search with sentence-transformers, wiki graph database, and per-tag novelty thresholds.
- **EvoX Meta-Evolution (Offline Pipeline)**: Two-level evolution — an inner loop evolves candidate solutions under a search strategy; an outer loop meta-evolves the strategy itself (as executable Python code) when progress stagnates. Multi-objective proxy scoring, UCB parent selection, `vibe evox run` CLI. `--target harness` evolves the agent's own memory/reflection config knobs and prompt variants against the eval suite, accepted only through a >5% regression gate vs the baseline scorecard.
- **Shadow Workspace Rollbacks**: Auto-creates hidden git branch (`vibe/shadow-<session-id>`) before write-heavy operations; one-command restore on failure.
- **Multi-Agent Swarm**: DAG-based orchestration of specialized sub-agents (Research, Coding, Critic, Planner) with Pub/Sub message bus and shared wiki.
- **Adversarial Red-Team Harness**: Built-in 3-tier adversarial security testing (`vibe/redteam/`, run via `python scripts/run_redteam.py`) covering 6 attack surfaces (S1 Bash patterns, S2 File jail, S3 SSRF, S4 SmartApprover prompt injection, S5 Skill supply chain, S7 MCP bridge). Tier A is a fully offline component attack matrix, Tier B contains jailbroken-mock-LLM scenarios, Tier C (`--live`) probes a real model inside an OS-jailed victim harness.
- **React Trace Dashboard**: Web UI for session observability — timeline, wiki graph, telemetry charts, system stats. Dark theme, real-time WebSocket updates.
- **Preference Layer**: 8 persistent heuristics converting user feedback into agent behavior — tool defaults, approval rules, style, macros, recovery, compaction, provider routing, extraction.

Most roadmap milestones are marked complete in `docs/ROADMAP.md`; Phase 4.1 (RLM training pipeline) is the section currently marked in progress. EvoX was added afterwards as an offline pipeline (see `docs/EvoX_implementation.md`).

---

## 2. Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Package Manager | `pip` (editable install) or `uv` (lockfile: `uv.lock` present) |
| CLI Framework | Typer + Rich console; `prompt_toolkit` for the TUI |
| Validation | Pydantic v2 + pydantic-settings |
| HTTP Clients | aiohttp, httpx |
| Web Dashboard | FastAPI + Uvicorn (optional extra `[api]`) |
| Frontend | React 18 (CDN-loaded, no build step) + D3.js + Recharts |
| Document Ingestion | docling (memory ingestion pipeline) |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`, 384-dim) with fastText/keyword fallback |
| Templating | Jinja2 |
| Testing | pytest + pytest-asyncio |
| Linting / Formatting | ruff + black + mypy |
| CI/CD | GitHub Actions (`.github/workflows/ci.yml`) |

Core dependencies (`pyproject.toml`): typer, rich, pydantic, pydantic-settings, aiohttp, httpx, pyyaml, filelock, numpy, jinja2, docling, prompt_toolkit, google-genai. Note that `pytest`/`pytest-asyncio` are declared as runtime dependencies, not dev-only. Playwright is an optional, manually-installed dependency used only by the browser tool's Tier 2 (`pip install playwright && playwright install chromium`).

---

## 3. Project Structure

```
vibe-agent/
├── pyproject.toml              # Project config, deps, tool settings (setuptools build)
├── uv.lock                     # uv lockfile (optional)
├── vibe/                       # Main source package
│   ├── __main__.py             # Entry point: `python -m vibe`
│   ├── cli/
│   │   ├── main.py             # Typer CLI root (`vibe` command, ~2,480 lines; all subcommands)
│   │   ├── tui.py              # prompt_toolkit TUI (VibeTUI)
│   │   ├── input_buffer.py     # Interactive input handling
│   │   ├── rendering.py        # Markdown/panel rendering helpers
│   │   └── skill_commands.py   # `vibe skill *` subcommands
│   ├── core/                   # Query loop, model gateway, config, coordinators
│   │   ├── query_loop.py       # ~1,970-line state machine (IDLE → PLANNING → ... → COMPLETED)
│   │   ├── model_gateway.py    # Multi-provider LLM gateway with circuit breakers
│   │   ├── query_loop_factory.py  # Wires all components together
│   │   ├── config.py           # Hierarchical config (default → ~/.vibe/config.yaml → env)
│   │   ├── coordinators.py     # ToolExecutor, FeedbackCoordinator, SecurityCoordinator, etc.
│   │   ├── context_compactor.py / context_planner.py            # Context window management
│   │   ├── cost_tracker.py / cost_router.py / latency_tracker.py  # Cost-aware routing
│   │   ├── provider_registry.py / shared_circuit_breaker.py
│   │   ├── session_controller.py / session_recovery.py            # Durable sessions
│   │   ├── adaptive_budget.py / conversation_queue.py / sub_agent.py
│   │   └── error_recovery.py / health_check.py / logger.py / llm_types.py
│   ├── adapters/               # LLM provider adapters (base, openai, anthropic, gemini, registry)
│   ├── harness/                # Harness-level components
│   │   ├── planner.py          # ContextPlanner (keyword + embedding + LLM routing)
│   │   ├── dag_planner.py      # DAG-based parallel task planner
│   │   ├── constraints.py      # Hook pipeline (PRE_VALIDATE → POST_FIX)
│   │   ├── feedback.py         # FeedbackEngine for quality gating
│   │   ├── instructions.py     # System prompt assembly
│   │   ├── embeddings.py       # Unified embedding loader (singleton + LRU cache)
│   │   ├── mcp_router.py       # MCP tool routing
│   │   ├── orchestration/      # sync_delegate.py (sub-agent delegation)
│   │   ├── security/           # redactor.py
│   │   ├── skills/             # Skill system v2
│   │   │   ├── parser.py       # TOML frontmatter + markdown parser
│   │   │   ├── models.py       # Pydantic Skill models
│   │   │   ├── validator.py    # Security scanning
│   │   │   ├── executor.py     # Step execution with variable substitution
│   │   │   ├── installer.py    # Atomic install from git/tarball/local
│   │   │   ├── maker.py        # Self-improving Skill-Maker pipeline
│   │   │   ├── maker_config.py # SkillMakerConfig (wired into VibeConfig.skill_maker)
│   │   │   ├── orchestrator.py # Inter-skill await + sub-agent spawn
│   │   │   └── marketplace.py / dynamic_tools.py / typed_vars.py / approval.py
│   │   └── memory/             # Harness memory layer
│   │       ├── trace_store.py  # Session persistence (SQLite/JSON/Memory)
│   │       ├── eval_store.py   # Eval result storage
│   │       └── session_store.py
│   ├── memory/                 # Tripartite memory system
│   │   ├── extraction.py       # Async knowledge extraction
│   │   ├── reflection.py       # TrajectoryReflector — post-session lesson curation
│   │   ├── compaction.py       # LessonCompactor — merges lesson pages into principles
│   │   ├── wiki.py             # Wiki page CRUD + FlashLLM contradiction
│   │   ├── pageindex.py        # Vector-based routing index
│   │   ├── wiki_graph.py       # Entity-relationship graph
│   │   ├── vector_index.py     # sentence-transformers index with keyword fallback
│   │   ├── semantic_dedup.py   # Vector similarity deduplication
│   │   ├── novelty_thresholds.py / compiler.py / flash_client.py / models.py
│   │   ├── rlm_analyzer.py     # Telemetry-driven LoRA trigger analysis
│   │   ├── rlm_trainer.py / _rlm_train_worker.py      # LoRA fine-tuning launcher
│   │   ├── ingestion/          # Document ingestion (chunker, parser, worker)
│   │   └── shared_db.py / rate_limiter.py / telemetry.py / telemetry_collector.py
│   ├── evox/                   # EvoX meta-evolution (offline pipeline)
│   │   ├── loop.py             # MetaEvolutionLoop (core two-level algorithm)
│   │   ├── strategy.py / strategy_code.py  # Evolvable strategies as executable code
│   │   ├── population.py / generators.py / metrics.py / types.py
│   │   ├── evaluators.py / circle_packing.py / tsp.py  # Benchmarks
│   │   ├── harness_target.py   # `--target harness`: bounded harness knob/prompt evolution + regression gate
│   │   └── cli.py              # `vibe evox run` command (evox_app Typer sub-app)
│   ├── redteam/                # Adversarial security testing harness
│   │   ├── orchestrator.py     # Tier-A attack matrix runner (rides swarm EventBroker)
│   │   ├── corpus.py           # Corpus YAML loading + fail-fast schema validation
│   │   ├── corpus/             # Attack payloads: s1_bash_patterns … s7_mcp (YAML)
│   │   ├── oracles.py          # Executors + outcome oracles per attack surface
│   │   ├── tier_b.py / tier_3.py  # Compromised-model scenarios / extended tiers
│   │   ├── victim.py           # OS-jailed victim harness for live probes
│   │   ├── live.py             # Tier-C live model probes (--provider kimi|gemini)
│   │   └── report.py           # JSON/Markdown findings report
│   ├── tools/                  # Tool system + security
│   │   ├── bash.py             # Sandboxed Bash (subprocess_exec, no shell)
│   │   ├── browser.py          # Adaptive Dual-Tier Browser (static/Playwright, SSRF guard)
│   │   ├── file.py             # Jailed File operations
│   │   ├── tool_system.py      # Tool registry and execution
│   │   ├── git_shadow.py       # Shadow workspace manager
│   │   ├── mcp_bridge.py       # MCP protocol bridge
│   │   ├── task_verifier.py    # Post-task verification
│   │   ├── skill_install.py / skill_manage.py / skill_runner.py  # Skill tools
│   │   └── security/           # 5-layer security components
│   │       ├── patterns.py     # Dangerous command regex scanner
│   │       ├── file_safety.py  # Path traversal protection
│   │       ├── human_approval.py / approval_store.py
│   │       ├── smart_approver.py
│   │       ├── checkpoints.py  # Rollback point manager
│   │       ├── env_sanitizer.py / skills_guard.py / permission_audit.py
│   │       └── audit.py        # Security event audit log
│   ├── swarm/                  # Multi-agent orchestration
│   │   ├── protocol.py         # Pub/Sub EventBroker + MessageBus
│   │   ├── agent.py            # SubAgent lifecycle + roles
│   │   ├── orchestrator.py     # TaskDAG scheduler
│   │   └── shared_wiki.py      # Coordinated wiki writes
│   ├── preferences/            # 8-domain preference layer
│   │   ├── registry.py         # SQLite WAL-backed persistence
│   │   ├── tool_prefs.py / approval_rules.py / style_policy.py
│   │   ├── macro_session.py    # YAML multi-step workflows
│   │   └── recovery_rules.py / compaction_policy.py / provider_prefs.py / extraction_policy.py
│   ├── evals/                  # Evaluation infrastructure
│   │   ├── runner.py           # EvalRunner (core engine)
│   │   ├── factory_runner.py   # Fresh QueryLoop per case
│   │   ├── adversarial.py      # Prompt injection / jailbreak tests
│   │   ├── soak_test.py        # Long-running stress tests
│   │   ├── multi_model_runner.py / multi_provider_benchmark.py / model_registry.py
│   │   ├── judge.py / regression.py / dashboard.py / observability.py
│   │   ├── EVAL_SUITE.md       # Eval suite documentation
│   │   └── builtin/            # 50 YAML eval case definitions
│   └── dashboard/              # FastAPI backend + React frontend
│       ├── server.py           # FastAPI server (WebSocket, token auth)
│       └── static/             # index.html, app.js, style.css (no build step)
├── tests/                      # ~1,960 test functions across 172 files; mirrors vibe/ layout
├── scripts/
│   ├── ci_eval_report.py       # CI regression check + markdown report
│   ├── validate_eval_tags.py   # Eval YAML schema validator
│   ├── run_redteam.py          # Red-team suite entry point (Tier A+B offline, --live for Tier C)
│   ├── validate_redteam_corpus.py  # Red-team corpus YAML validator
│   └── evox_self_evolution_demo.py # EvoX demo script
├── docs/                       # ARCHITECTURE.md, CONFIGURATION.md, EVALUATION.md, ROADMAP.md,
│                               # EvoX_implementation.md, MEMORY_DESIGN.md, TRIPARTITE_DESIGN.md,
│                               # DASHBOARD.md, UI.md, SECURITY_HARNESS_IMPROVEMENTS.md,
│                               # baseline_scorecard.json, redteam_report.md/json, sample_config.yaml, ...
├── skills/                     # Local skill directory (code-auditor, db-migrator, dependency-auditor,
│                               #   git-workflow, log-analyst, refactor-verifier, stock-analysis)
├── wiki/                       # Session wiki pages (markdown)
├── logs/                       # Session logs
├── .agents/                    # Kimi Code project customizations: agents/ (sub-agent prompts),
│                               #   hooks/ (lifecycle hook scripts — see .agents/README.md)
└── archive/                    # Reference implementations from earlier phases (do not modify)
```

---

## 4. Build and Test Commands

### Install (Development)
```bash
# Editable install with dev dependencies
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

# Dashboard (dev mode)
vibe dashboard start --port 8080 --no-auth

# EvoX meta-evolution
vibe evox run

# Session / shadow / preference / wiki management
vibe session list | cleanup | resume
vibe shadow list | create | restore | rollback | clean
vibe memory wiki list | search | show | create | edit | expire | compact | compile | review
vibe memory traces | status | import
vibe pref tool-set | tool-list | style-set | approval-list | ...
vibe macro list | run | create
```

### Testing
```bash
# Run full test suite (pytest-asyncio auto mode enabled in pyproject.toml)
pytest -x --tb=short -q

# Run with coverage
pytest --cov=vibe --cov-report=term-missing

# Run a specific subsystem
pytest tests/memory/ -v
pytest tests/core/test_query_loop.py -v
```

### Linting and Formatting
```bash
black vibe/ tests/
ruff check vibe/ tests/
ruff format vibe/ tests/
mypy vibe/
```

### Evaluations
```bash
# Run built-in eval suite
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

### Red-Team Suite
```bash
# Offline Tier A (attack matrix) + Tier B (compromised-model scenarios);
# writes docs/redteam_report.json / docs/redteam_report.md;
# exit code 1 if any defense check fails
python scripts/run_redteam.py

# Additionally run Tier C live probes against a managed endpoint
python scripts/run_redteam.py --live --provider kimi   # or gemini

# Validate corpus YAMLs
python scripts/validate_redteam_corpus.py
```

---

## 5. Code Style Guidelines

- **Line length**: 100 characters (`tool.black.line-length = 100`, `tool.ruff.line-length = 100`)
- **Target Python**: 3.11+ (`tool.black.target-version = ['py311']`)
- **Import style**: ruff-enforced (`select = ["E", "F", "I", "W"]`)
- **Type hints**: Use throughout; `mypy` runs with `ignore_missing_imports = true`
- **Async-first**: The QueryLoop and most core components are `async`. Use `asyncio` patterns.
- **Pydantic v2**: All data models use Pydantic v2. Avoid v1 compatibility syntax.
- **Docstrings**: Use Google-style or descriptive docstrings for public APIs.
- **Constants**: Uppercase for module-level constants; private helpers prefixed with `_`.
- **Error handling**: Prefer explicit exception types over bare `except`. Use `ErrorRecovery` and `RetryPolicy` for transient failures.

---

## 6. Testing Strategy

- **Test count**: ~1,960 test functions across 172 test files (`tests/`), with subdirectories mirroring `vibe/` (`tests/core/`, `tests/memory/`, `tests/evox/`, `tests/redteam/`, `tests/tools/security/`, ...).
- **Framework**: pytest with `pytest-asyncio` in auto mode (`asyncio_mode = "auto"` in `pyproject.toml`) — async test functions need no decorator.
- **Structure**: Mirror the `vibe/` package under `tests/` (e.g. `vibe/core/config.py` → `tests/core/test_config.py`).
- **Test types**:
  - **Unit tests**: Per-module, heavy use of `unittest.mock.AsyncMock` and `MagicMock`.
  - **Integration tests**: Query loop end-to-end, memory system integration, tool security integration.
  - **Security tests**: Dedicated `tests/tools/security/` for each security layer.
  - **Red-team tests**: `tests/redteam/` covers corpus loading, oracles/executors, orchestrator reporting, Tier B/Tier 3 scenarios, live probes, and victim isolation.
  - **Eval tests**: Runner, adversarial, factory isolation, soak tests.
- **Factory-per-case**: For eval isolation, always create a fresh `QueryLoop` via `QueryLoopFactory` rather than reusing instances. This prevents state bleed.
- **CI** (`.github/workflows/ci.yml`) runs three jobs:
  1. **lint**: `ruff check` + `ruff format --check` (Python 3.11)
  2. **unit-test**: `pip install -e ".[dev,api]"` then `pytest -x --tb=short -q` on Python 3.11, 3.12, 3.13
  3. **eval**: on pushes and non-draft PRs (Python 3.13); runs only if the configured model endpoint (`VIBE_BASE_URL`) is reachable; executes `python -m vibe.cli.main eval run --limit 20`, then `scripts/ci_eval_report.py`, and posts the markdown report as a PR comment.
- **Regression gate**: CI fails if eval score drops >5% below `docs/baseline_scorecard.json`.

---

## 7. Security Considerations

This project handles LLM tool execution, file system access, and API keys. Security is not an afterthought.

- **5-Layer Defense** (in order):
  1. **PatternEngine**: Regex denylist (`sudo`, `rm -rf /`, etc.) in `vibe/tools/security/patterns.py`
  2. **FileSafetyGuard**: `_resolve_and_jail()` prevents path traversal even via symlinks.
  3. **HumanApprover**: `manual` / `smart` / `auto` approval modes. Configurable via `security.approval_mode`.
  4. **SmartApprover**: LLM-based risk assessment for benign false positives (with optional veto-only `fast_gate` pre-filter; never early-approves).
  5. **CheckpointManager**: Creates rollback points before destructive ops.
- **Secret Redaction**: Automatic stripping of API keys (OpenAI, AWS, GitHub, Slack, Google, Stripe, Discord, JWTs, private keys) and passwords from trace stores, logs, and eval stores. Never persist raw credentials.
- **Env Sanitization**: Blocks path overrides, strips shell env, blocks secret-prefix env vars (`vibe/tools/security/env_sanitizer.py`).
- **Bash Sandbox**: Uses `subprocess_exec` (no shell) with timeout and regex denylist.
- **SSRF Protection**: The browser tool blocks loopback/private/metadata networks.
- **Fail Closed**: `security.fail_closed = true` — any security component failure defaults to deny.
- **Skill Validation**: `SkillValidator` scans for filesystem destruction, pipe-to-shell, eval injection, suspicious URLs, and hardcoded credentials before installation.
- **Shadow Workspace**: Git shadow branches provide a rollback safety net for write-heavy sessions.
- **Red-Team Harness**: `vibe/redteam/` continuously attacks the defense layers offline (6 surfaces: bash patterns, file jail, SSRF, SmartApprover prompt injection, skill supply chain, MCP bridge). Run it after any security-relevant change; it exits non-zero if a defense check fails. Findings land in `docs/redteam_report.md/json`.
- **Dashboard**: Binds to 127.0.0.1 by default, token auth, strict CORS, read-only API.

**Agent rule**: Do not weaken security defaults, skip validation, or disable redaction in production code. Any security change must include tests in `tests/tools/security/` and, where an attack surface is affected, a corpus entry in `vibe/redteam/corpus/`.

---

## 8. Configuration

The agent reads configuration from:
1. **Built-in defaults** (in `vibe/core/config.py`)
2. **`~/.vibe/config.yaml`** (user config)
3. **Environment variables** (`VIBE_*`)

Top-level sections of `VibeConfig` (Pydantic models in `vibe/core/config.py`, except `skill_maker` which lives in `vibe/harness/skills/maker_config.py`):
- `llm` / `model` — Default model, base URL, API key, temperature, fallback chain, streaming
- `planner` — Context planner (embeddings, LLM routing, DAG execution)
- `security` — Approval mode plus `file_safety`, `env_sanitization`, `sandbox`, `audit`, and `fast_gate` sub-models
- `memory` — Tripartite memory (`wiki`, `pageindex`, `rlm`, `flash`, `reflection` sub-models)
- `trace_store` — Session persistence backend (`sqlite`, `json`, or `memory`)
- `eval` — Eval runner settings
- `cost_router` — Cost limits and latency-aware routing
- `session` — Durable session checkpoints / recovery
- `error_recovery` — Pivotal retry (`pivotal_retry_enabled`, `max_pivotal_retries`)
- `preferences` — Preference layer settings
- `skill_maker` — Self-improving pipeline settings
- `shadow_workspace` — Git shadow branch settings (`enabled`, `auto_rollback`)
- `logging` — Log dir, rotation, retention, level

See `docs/sample_config.yaml` for a complete example and `docs/CONFIGURATION.md` for reference.

---

## 9. Key Conventions for Agents

### Before Making Changes
1. **Read the relevant module's docstring and comments** — the codebase is heavily commented.
2. **Check `docs/ARCHITECTURE.md`** for component relationships and design philosophy.
3. **Check `docs/ROADMAP.md`** to understand completed phases and priorities.
4. **Run existing tests** for the module you are touching: `pytest tests/<module>/ -v`

### When Adding Features
- **Mirror test structure**: If you add `vibe/new_module.py`, add `tests/test_new_module.py`.
- **Use Pydantic models** for all new data structures.
- **Prefer async**: If the feature interacts with the QueryLoop, ModelGateway, or tools, use `async`/`await`.
- **Add eval cases** if the feature changes agent behavior: create a YAML in `vibe/evals/builtin/` (validated by `scripts/validate_eval_tags.py`).
- **Update config schema** in `vibe/core/config.py` if new settings are needed.
- **Security review**: Any tool or skill change needs a security test.

### When Fixing Bugs
- **Add a regression test** that fails before the fix and passes after.
- **Check eval suite**: Run `vibe eval run` to ensure no regressions.
- **Minimal changes**: Prefer surgical fixes over broad refactors.

### File Modification Rules
- Do not modify files outside the working directory unless explicitly instructed.
- Do not commit or push git changes unless explicitly asked.
- Do not modify `archive/` — it holds reference implementations from earlier phases.
- Do not run destructive bash commands without understanding the denylist patterns.

---

## 10. Useful References

| Document | Purpose |
|----------|---------|
| `README.md` | Quick start, feature overview, CLI examples |
| `docs/ARCHITECTURE.md` | System philosophy, component deep-dives, state machine flow |
| `docs/CONFIGURATION.md` | Full config reference with environment variable overrides |
| `docs/EVALUATION.md` / `vibe/evals/EVAL_SUITE.md` | Eval case YAML schema, assertion types, running instructions |
| `docs/ROADMAP.md` | Completed milestones and phase history |
| `docs/EvoX_implementation.md` | EvoX meta-evolution algorithm details |
| `docs/MEMORY_DESIGN.md` / `docs/TRIPARTITE_DESIGN.md` | Memory system design |
| `docs/DASHBOARD.md` / `docs/UI.md` | Dashboard and UI architecture |
| `docs/SECURITY_HARNESS_IMPROVEMENTS.md` / `docs/redteam_report.md` | Red-team design and latest findings |
| `docs/CHANGELOG.md` | Version history |
| `pyproject.toml` | Dependencies, build config, pytest/black/ruff/mypy settings |
| `scripts/ci_eval_report.py` | CI regression gate logic |
| `scripts/validate_eval_tags.py` / `scripts/validate_redteam_corpus.py` | YAML validators for CI |
| `scripts/run_redteam.py` | Red-team suite entry point |
