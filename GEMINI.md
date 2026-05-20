# Vibe Agent Project Context & Gemini Onboarding Guide

Vibe Agent is a high-performance, resilient, visual-first, and LLM-agnostic autonomous CLI agent harness platform (Python 3.11+). 

Unlike most agent frameworks that prioritize LLM models, Vibe Agent treats the **harness** as the core product—enforcing rigorous security, sandboxed execution, context efficiency, self-improvement, and multi-agent coordination with a complete validation and evaluation suite.

---

## 🏗️ Architecture & Core Components

Vibe Agent's architecture decouples critical coordinator concerns from the orchestrator state machine to achieve maximum reliability, tool isolation, and empirical validation.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    User Interface Layer                                 │
│    (Typer CLI + Rich  │  React Dashboard  │  Swarm Orchestrator)       │
└────────────────────────┬────────────────────┬───────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              Query Loop / State Machine (QueryState)                    │
│   (Context Planner, Compactor, Hook Pipeline, Security, Preferences)    │
└──────────────┬─────────────┬─────────────┬────────────────┬─────────────┘
               │             │             │                │
               ▼             ▼             ▼                ▼
┌───────────────────┐ ┌─────────────┐ ┌────────────────┐ ┌───────────────┐
│   Model Gateway   │ │ Tool System │ │  Memory Stack  │ │   Swarm       │
│ (Multi-Provider,  │ │ (Bash, File,│ │ (TraceStore,   │ │ (SubAgents,   │
│  Fallback, CB,    │ │  Skills,    │ │  Wiki, Eval,   │ │  TaskDAG,     │
│  Cost Router)     │ │  MCP Bridge)│ │  Telemetry)    │ │  EventBroker) │
└───────────────────┘ └─────────────┘ └────────────────┘ └───────────────┘
        │                                              │
        ▼                                              ▼
┌───────────────────┐                      ┌───────────────────────────┐
│  Skill-Maker      │                      │  Shadow Workspace         │
│  (Pattern Detect, │                      │  (Git shadow branches,    │
│   LLM Generate,   │                      │   auto-restore on fail)   │
│   Validate,       │                      └───────────────────────────┘
│   Propose)        │
└───────────────────┘
```

### 1. The Query Loop State Machine (`vibe/core/query_loop.py`)
Drives the thought-action loop using the `ConversationStateMachine` with validated transitions (`StateTransitionError` is raised on invalid jumps).
*   **QueryState Enum**: `IDLE` → `PLANNING` → `PROCESSING` → `TOOL_EXECUTION` → `SYNTHESIZING` → `COMPLETED | INCOMPLETE` (max iterations exhausted) | `STOPPED` | `ERROR`.
*   **Three Coordinators** manage discrete components:
    *   **`ToolExecutor`**: Executes tool calls sequentially with exception isolation, applying the `HookPipeline` (`PRE_VALIDATE → PRE_MODIFY → PRE_ALLOW → POST_EXECUTE → POST_FIX`). Auto-creates shadow workspaces on write-heavy tasks.
    *   **`CompactionCoordinator`**: Manages context limits before LLM calls using `tiktoken` (cl100k_base). Employs 4 strategies in order: `TRUNCATE` (default), `LLM_SUMMARIZE`, `OFFLOAD`, and `DROP`. Cap size-heavy individual messages to `max_chars_per_msg` (default 4000).
    *   **`FeedbackCoordinator`**: Structured `FeedbackEngine` quality gate. Catches low-quality output and injects retry hints. Note: returns a minimum floor of `FeedbackResult(score=0.5)` on exception.

### 2. Model Gateway & Resiliency (`vibe/core/model_gateway.py`)
Acts as a multi-provider adapter gateway shielding the agent from upstream LLM outages.
*   **Providers & Models**: Resolves endpoints dynamically via `ProviderRegistry`. Fully supports OpenAI, Anthropic, OpenRouter, Ollama, and Kimi.
*   **Circuit Breakers**: State-aware circuit breaking (opens after 5 consecutive failures, 60s cooldown). Shared with FlashLLM via `SharedCircuitBreaker`.
*   **Fallback Chains**: Lazily switches to the next model in a chain on model error (rate-limiting `429` errors do **not** trip fallbacks).
*   **Cost & Latency Routing**: Includes a `CostTracker` enforcing budget limits and a `LatencyAwareRouter` selecting models using rolling p50/p95 statistics.

### 3. Zero-Trust Tool & Security System (`vibe/tools/`)
Tool safety is guaranteed using a fail-closed (`security.fail_closed = true`) defense-in-depth design.
*   **5-Layer Security Defense**:
    1.  **PatternEngine**: Regular expression denylist (`sudo`, `rm -rf /`, `curl | bash`, fork bombs) in `vibe/tools/security/patterns.py`.
    2.  **FileSafetyGuard**: `_resolve_and_jail()` prevents path traversal even through symlinks.
    3.  **HumanApprover**: Approval modes (`manual` prompts every time; `smart` evaluates benign false-positives via LLM; `auto` runs directly).
    4.  **SmartApprover**: LLM-driven safety grading.
    5.  **CheckpointManager**: Creates disk and system restore points before executing changes.
*   **Bash Sandbox**: Executes via `subprocess_exec` (`shell=False`) for maximum control.
*   **MCP Routing**: Prefix-based routing rules and failovers managed via `MCPRouter` with healthy pings, cooldowns, and automatic failovers.
*   **Shadow Workspace (`vibe/tools/git_shadow.py`)**: Hidden git branch (`vibe/shadow-<session-id>`) created before the first write-heavy command in a session. On `ERROR` or `INCOMPLETE`, logs a rollback hint to allow full restoration.

### 4. Skill System v2 & Skill-Maker (`vibe/harness/skills/`)
Skills are declared using `SKILL.md` (TOML frontmatter `+++` and Markdown documentation).
*   **Parser & Validator**: Parses, scans for security violations (phishing, filesystem destruction, suspicious URLs), and atomically installs/uninstalls.
*   **Typed Variables**: `TypedSkillExecutor` enforces strict coercion, schema check, and defaults.
*   **Orchestration**: `SkillOrchestrator` allows skills to await other skills or parallelized sub-agent tasks using `asyncio.gather`.
*   **Autonomous Skill-Maker (`maker.py`)**: A self-improving pipeline running at completion. It detects recurring wiki tags, draft-generates new skills, sandbox-validates them, and proposes atomic installation to the user.

### 5. Tripartite Memory System (`vibe/memory/`)
A three-tier persistent context memory designed for local efficiency:
*   **LLMWiki (`wiki.py`)**: Markdown files stored in a folder with backlink resolution and file locking. Uses FlashLLM local models (`qwen3:1.7b` on Ollama) for async contradiction detection.
*   **PageIndex (`pageindex.py`)**: Unified vector index powered by local `sentence-transformers` (`all-MiniLM-L6-v2`) with fastText and BM25 fallback.
*   **RLM Analyzer (`rlm_analyzer.py`)**: Telemetry-driven analyser that evaluates session token footprint and triggers background fine-tuning (LoRA) via subprocess workers when thresholds are crossed.

### 6. Swarm Multi-Agent Orchestration (`vibe/swarm/`)
Provides DAG-based scheduling of specialized agent roles (RESEARCH, CODING, CRITIC, PLANNER) coordinated via `AgentProtocol` and Pub/Sub EventBroker queues.

---

## 🛠️ Key Commands & CLI Reference

### 1. Installation & Environment Setup
```bash
# Editable developer installation
pip install -e ".[dev]"

# Optional Extras
pip install -e ".[api]"      # FastAPI server
pip install -e ".[memory]"   # Vector search / sentence-transformers
pip install -e ".[rlm]"      # LoRA fine-tuning libraries
```

### 2. Execution & Observability
```bash
# Interactive chat shell
python -m vibe

# One-shot query
python -m vibe "Explain core loop state transition in vibe"

# Start the React Trace Dashboard
vibe dashboard start --port 8080 --no-auth
```

### 3. Verification & Testing
```bash
# Run pytest with auto asyncio (1400+ tests)
pytest

# Skip known-broken config signature tests during unrelated development:
pytest --ignore=tests/test_config.py --ignore=tests/test_config_providers.py --ignore=tests/core/test_config_security.py

# Lint & formatting check
ruff check vibe/ tests/
ruff format --check vibe/ tests/
```

### 4. Evaluation & Regression Gate
```bash
# Run all built-in eval cases
vibe eval run

# Run evals restricted to a specific subsystem
vibe eval run --tag subsystem=memory

# Soak stress test
vibe eval soak --duration 30 --cpm 6

# Standalone E2E benchmark
python run_e2e_evals.py
```

### 5. Skill & Memory Management CLI
```bash
# Skill management
vibe skill list
vibe skill validate <path-to-skill-directory>
vibe skill install <local-path-or-git-url>

# Memory & Wiki management
vibe memory status
vibe memory wiki list --status verified
```

---

## 📝 Critical Gemini Development Guidelines

When developing in this repository as a Gemini agent, follow these hard constraints:

### ⚠️ Sandboxing & Platform Quirks (macOS)
*   **SandboxManager**: For macOS, execution runs under a native `sandbox-exec` wrapper that defaults to `deny file-write*`.
*   **MCP & Skill Configuration**:
    *   Python (`.py`) skill entry points default to **Plugin loading**.
    *   Python MCP servers **require explicit `mcp` configuration** in `SKILL.md` to trigger sandbox configuration. Sandboxed MCP skills will run inside this jail.

### 🔌 Local Infrastructure & Observability
*   **Phoenix & OpenInference**: AWS, Opik, and ZenML dependencies have been entirely purged and replaced with a fully local stack.
*   **Tracing Configuration**: Observability is handled via OpenInference Phoenix integration configured in `src/tracing.py`.
*   **Local Execution**: Ensure Ollama is running (`qwen3:8b` as primary brain, `qwen3:1.7b` for flash operations) and Phoenix tracing is enabled. Start application scripts with:
    ```bash
    uv run main.py
    ```

### 🔄 Git Worktree Safety
*   **DO NOT** manually run `rm -rf` to delete a git worktree. This leaves stale entries in the git db and corrupts worktree metadata.
*   **Always** use standard git commands for worktree cleanups:
    ```bash
    git worktree remove <worktree-path>
    # or to prune stale worktree entries:
    git worktree prune
    ```

### 🧠 Code Style & Architecture Best Practices
*   **Line Limit**: Strictly enforce 100 characters max (`tool.black.line-length = 100`, `tool.ruff.line-length = 100`).
*   **Type Safety**: Use explicit type annotations everywhere. `mypy` is strictly audited in CI.
*   **Async-First**: Core Query Loop and coordinators are strictly async. Use `asyncio` patterns properly.
*   **No Placeholders**: When images or mockups are requested, use the `generate_image` tool instead of empty mocks.
*   **Safe Modifying Commands**: Skip testing known-broken configuration legacy tests (`test_config.py`, etc.) unless you are refactoring Pydantic config initialization.

---

## 📂 Key Directory Map

*   `vibe/core/`: The heart of the harness (QueryLoop, Config, Gateways, Coordinators, Recovery).
*   `vibe/adapters/`: Custom network API wrappers (`openai.py`, `anthropic.py`).
*   `vibe/tools/`: Bash sandbox, File jails, `MCPRouter` routing, and security auditing layers.
*   `vibe/memory/`: Tripartite implementation (PageIndex vector index, LLMWiki database, RLM LoRA trainer).
*   `vibe/swarm/`: Pub/Sub event broker, TaskDAG scheduling, and specialized swarm agents.
*   `vibe/preferences/`: Persisted SQLite WAL-backed user preference heuristics.
*   `vibe/evals/`: Extensible metrics engine, OTEL instrumentation, and built-in YAML test cases.
