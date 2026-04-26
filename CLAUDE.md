# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Vibe Agent is an LLM-agnostic agent harness (Python 3.11+). The product is the **harness**, not a model — work here is judged on resilience, tool safety, context efficiency, and eval scores rather than model capability. The Phase 1–3 hardening (planner, trace store, MCP routing, state machine, agent-as-judge, regression gate) is documented in `docs/SECURITY_HARNESS_IMPROVEMENTS.md` and is the canonical reference for the post-April-2026 architecture.

## Common Commands

```bash
# Install (editable, with dev extras)
pip install -e ".[dev]"

# Run the agent
python -m vibe                                    # interactive
python -m vibe "your one-shot query here"         # single query
vibe --model <name> --server <url> --debug ...    # alternative entry point

# Tests (~675 collected; 11 pre-existing failures in config tests — see notes below)
pytest                                            # full suite
pytest -x --tb=short -q                           # CI-style fast fail
pytest tests/test_query_loop.py                   # single file
pytest tests/test_query_loop.py::test_name        # single test
pytest -k "skill"                                 # by keyword
pytest --ignore=tests/test_config.py --ignore=tests/test_config_providers.py --ignore=tests/core/test_config_security.py
                                                  # skip known-broken config tests during unrelated work

# Lint / format (CI gates on these)
ruff check vibe/ tests/
ruff format --check vibe/ tests/
ruff format vibe/ tests/                          # auto-fix

# Eval suite
vibe eval run                                     # all built-in cases
vibe eval run --tag bash --limit 5                # filter
vibe eval soak --duration 60 --cpm 6              # long-running stress test
vibe eval update-baseline                         # regen docs/baseline_scorecard.json (intentional only)
python run_e2e_evals.py                           # standalone runner (also has benchmark/soak subcommands)
python scripts/validate_eval_tags.py              # validate eval YAML schema before commit

# Skills
vibe skill list / validate <path> / install <src> / run <id> key=val / uninstall <id>
```

CI runs lint → unit tests (Py 3.11/3.12/3.13) → eval suite with regression check (must stay within 5% of `docs/baseline_scorecard.json`). Update the baseline intentionally via `vibe eval update-baseline`, never to silence a regression.

### Pre-existing config test failures

`tests/test_config.py`, `tests/test_config_providers.py`, and `tests/core/test_config_security.py` collect-error against the current `VibeConfig.load()` signature (the Pydantic refactor removed legacy `path` / `auto_create` kwargs). These ~11 failures are **out of scope** for any task that isn't explicitly migrating those tests. Don't "fix" them by reverting the config schema; update the tests to the new constructor instead.

## Architecture

### Query loop = state machine, with an explicit ConversationStateMachine on top

`vibe/core/query_loop.py` (~370 lines) drives `IDLE → PLANNING → PROCESSING → TOOL_EXECUTION → SYNTHESIZING → COMPLETED|INCOMPLETE|STOPPED|ERROR`. Iteration cap (`max_iterations`, default 50) ends in `INCOMPLETE` — distinct from `COMPLETED`. Don't conflate them.

The new `vibe/harness/conversation_state.py` (`ConversationStateMachine`, `ConversationState`, `StateTransitionError`, `ConversationBranch`) sits alongside this with **validated** transitions — invalid jumps raise `StateTransitionError`. It also tracks per-state timeouts (planning 30s, tool 120s, user input 300s), `request_interrupt()` flag, branching for parallel tool execution, and a full transition history for debugging. The legacy `QueryState` enum is preserved for back-compat; new behavior should drive the explicit machine.

Three coordinators (`vibe/core/coordinators.py`) own pieces extracted from the loop:
- **`ToolExecutor`** — runs `HookPipeline` (PRE_VALIDATE → PRE_MODIFY → PRE_ALLOW → POST_EXECUTE → POST_FIX) around tool calls; sequential with per-call exception isolation. Now delegates MCP calls to `MCPRouter` when configured.
- **`CompactionCoordinator`** — checks token budget before every LLM call. Strategies: TRUNCATE (default), LLM_SUMMARIZE, OFFLOAD, DROP. Token estimation uses `tiktoken` (cl100k_base) with a chars/4 fallback.
- **`FeedbackCoordinator`** — scores responses via the structured `FeedbackEngine`; below threshold injects retry hint and continues. **Footgun still present:** on exception, `vibe/harness/feedback.py` returns `FeedbackResult(score=0.5)` silently (line ~202). Don't paper over real failures by relying on the 0.5 floor.

### Hybrid Planner (4-tier, replaces keyword-only `ContextPlanner`)

`vibe/harness/planner.py` exposes `HybridPlanner`, `PlanRequest`, `PlanResult`. Tiers escalate by cost:

1. **Keyword** (~1ms, free) — query words match tool/skill names/tags
2. **Embedding** (~10ms, local) — fastText `cc.en.50.bin` (5MB, no PyTorch dep), confidence threshold 0.6
3. **LLM router** (~500ms, paid) — only when embedding confidence is low
4. **Fallback** — keyword-only if `numpy`/`fasttext` are missing

Includes a SHA-256-keyed query cache (5min TTL) and trace-store memory injection (`system_prompt_append` of relevant past sessions). Don't add an LLM call to the planner without first confirming the lower tiers can't handle it.

### Two-tier LLM config: providers vs models

This trips people up. Editing `default_model` is rarely the right knob.

- **Providers** (`providers:` in `~/.vibe/config.yaml`) = endpoint + adapter (`openai` or `anthropic`) + API key env var + headers.
- **Models** (`models:`) = friendly names mapping to `provider + model_id`.
- **Fallback chain** references model names; `ProviderRegistry` resolves connection details lazily, so a chain can span providers (e.g. OpenRouter → Anthropic).
- If `providers:` is absent, `VibeConfig` synthesizes one from top-level `llm:` fields for back-compat.
- Adapters live in `vibe/adapters/` (`openai.py`, `anthropic.py`, `registry.py`); add new providers there.
- `VibeConfig` (in `vibe/core/config.py`) is Pydantic v2. Env override uses `VIBE_<FIELD>` and double-underscore for nesting (e.g. `VIBE_LLM__MODEL=gpt-4`). Full list in `docs/CONFIGURATION.md`.

`vibe/core/model_gateway.py` owns the resilience layer: per-model circuit breakers (default 5 failures → open, 60s cooldown), retries with jitter, structured-output JSON coercion, redacted debug logging. Rate-limit (429) errors deliberately do **not** trip fallback.

### MCP routing with health checks & failover

`vibe/harness/mcp_router.py` (`MCPRouter`) wraps `MCPBridge` with prefix-based routing rules (`router.add_routing_rule("filesystem/", "filesystem_server", priority=10)`), 30s health pings, 3-failure unhealthy threshold with 60s cooldown, EMA latency, and automatic fallback to any other server exposing the requested tool. `ToolExecutor` uses it transparently when `mcp_bridge` is configured.

### Tool system is zero-trust

- `vibe/tools/bash.py` uses `subprocess_exec` (no shell) plus a regex denylist (`sudo`, `rm -rf /`, `curl|bash`, fork bombs, etc.).
- `vibe/tools/file.py` uses `_resolve_and_jail()` to block path traversal even via symlinks.
- `vibe/tools/security/` holds the broader security layer: `patterns.py`, `human_approval.py`, `approval_store.py`, `audit.py`, `file_safety.py`, `permission_audit.py`. `approval_mode` is `manual | smart | auto` (smart uses an LLM to auto-approve benign matches).
- Hooks live alongside the executor in `vibe/harness/constraints.py`. Adding a security check usually means adding a hook stage entry, not editing the loop.

### Skills: markdown + TOML, with Jinja2 + env-var rendering

Skills (`vibe/harness/skills/`) are `SKILL.md` files: TOML frontmatter (`+++` delimited) declares metadata, triggers, and steps; markdown body is documentation. Pipeline:

```
SkillParser → Skill (Pydantic models) → SkillValidator (security scan) →
ApprovalGate (CLI/Auto/Reject) → SkillInstaller (atomic, with rollback) →
SkillExecutor (Jinja2 + env vars + shell hardening)
```

`SkillExecutor` (`vibe/harness/skills/executor.py`) supports full Jinja2 (`{{ var }}`, loops, filters) plus `${VAR}` / `$VAR` / `${VAR:-default}` env-var substitution. Shell hardening:

| Layer | Behavior |
|---|---|
| Denylist | `rm`, `mkfs`, `dd`, `chmod`, `chown`, `sudo`, `su`, `eval`, `exec` blocked |
| Patterns | `curl \| bash`, `> /dev/sda`, `$(rm`, `rm -rf /` rejected |
| Shell strategy | `shell=False` + `shlex.split()` for simple commands; `shell=True` only for builtins (`exit`, `cd`, `source`, `export`) with single-quoted content |
| Timeout | 30s hard kill |

`SandboxedEnvironment` is **not** used (would break legitimate templates); skill safety relies on install-time validation + user-provided context being the only dynamic input. The regex denylist can be bypassed with creative encoding (e.g. `$(printf '%s' rm)`) — treat skill execution as a high-risk surface and prefer container/firejail isolation for untrusted skills (planned).

Installation supports git, tarball (zip-slip protection), and local paths. CLI lives in `vibe/cli/skill_commands.py`; agent-facing tool is `vibe/tools/skill_manage.py`.

### Trace store: 3 backends, time-gated cleanup

`vibe/harness/memory/trace_store.py` exposes `BaseTraceStore` with three implementations:

| Backend | Use case | Persistence | Vector search |
|---|---|---|---|
| `SQLiteTraceStore` | Production | SQLite + `sqlite-vec` | Cosine similarity |
| `JSONTraceStore` | Local dev | JSONL file | Linear scan |
| `MemoryTraceStore` | Unit tests | In-memory | Linear scan |

Cleanup is **time-gated** (`_should_cleanup()` checks `time.time() - _last_cleanup_time` against a 5-minute default), not per-write — keeps the hot path O(1). Embeddings are computed locally via fastText; **no PII redaction** in the store today, so don't log raw user content into traces if it might contain credentials.

### Eval-driven, not vibes-driven

`vibe/evals/builtin/*.yaml` (~47 cases) is the contract.

- `runner.py` (`EvalRunner`) — **now factory-per-case**: each `EvalCase` may set `case.metadata["query_loop_factory"]`, otherwise `default_factory` is used. `asyncio.Semaphore(max_concurrency=3)` bounds parallelism. OpenTelemetry-style spans for `eval_case`, `llm_call`, `tool_execution`, `assertion_check`.
- `multi_model_runner.py` — comparative scorecards, fresh `QueryLoop` per model.
- `soak_test.py` — long-running stress test, fresh `QueryLoop` per case, compares first-20% vs last-20% latencies for degradation.

11 assertion types: `file_exists`, `file_contains`+`contains_text`, `stdout_contains`, `response_contains`, `response_contains_any`, `min_response_length`, `tool_called`, `tool_sequence`, `no_tool_called`, `context_truncated`, `metrics_threshold`. Persistence: SQLite at `~/.vibe/memory/evals.db`. Run `python scripts/validate_eval_tags.py` after editing eval YAML — CI relies on the schema (required keys, valid difficulty/subsystem/category, unique IDs).

#### Agent-as-Judge (`vibe/evals/judge.py`)

`AgentJudge` lets a second LLM grade responses against weighted rubrics (correctness 2.0×, completeness 1.5×, safety 2.0×, helpfulness 1.0×), 0–5 per criterion, normalized to 0–100, default pass threshold 70. Low temperature (0.1), JSON output enforced, markdown-fence extraction, graceful malformed-JSON fallback. **Use a different model for judge vs agent** to avoid self-grading bias.

#### Regression gate (`vibe/evals/regression.py`)

`RegressionGate.from_file("docs/baseline_scorecard.json").check(current_results)` returns a report with regressions, improvements, and `passed`. Default thresholds: pass-rate max 5% drop with 70% absolute floor; avg score max 5%; tokens max +10%; p95 latency max +20%. Direction-aware (lower is better for tokens/latency). Per-case regressions flag a case that passed in baseline but fails now, regardless of aggregates.

### State on disk

User state lives under `~/.vibe/`:
- `config.yaml` — see `docs/sample_config.yaml`
- `history` — readline history for interactive mode
- `logs/` — session logs and `security.log` audit
- `memory/` — `traces.db`, `evals.db`
- `wiki/` — markdown knowledge base
- `scorecards/` — per-run JSON+MD reports

## Repository Conventions

- Python 3.11+, type hints on public APIs, Pydantic v2 for models.
- `ruff` line length 100; `select = ["E", "F", "I", "W"]`.
- `pytest-asyncio` in `auto` mode — async tests don't need a marker.
- Docs are part of the deliverable: `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/EVALUATION.md`, `docs/ROADMAP.md`, `docs/REVIEWS.md`, `docs/SECURITY_HARNESS_IMPROVEMENTS.md` (the Phase 1–3 reference) are kept current. `WIKI.md` is the long-form view; `CHANGELOG.md` follows Keep-a-Changelog loosely.
- `archive/` holds reference implementations and is inactive — don't import from it.
- Untracked `PLAN_*.md` and `docs/plans/*.md` are working planning docs; treat them as scratch unless the user references them.
