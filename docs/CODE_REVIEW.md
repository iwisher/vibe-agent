# Code Review Report — vibe-agent

**Date:** 2026-07-10
**Reviewer:** Kimi Code CLI (delegated thorough review)
**Scope:** Recent changes, core architecture, EvoX implementation, security layers, docs/plans alignment

---

## Summary

| Category | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 5 |
| LOW | 7 |

**Overall recommendation:** REQUEST CHANGES

The recent CI fixes (action bumps, lint cleanup, `QueryLoop.copy()` fix, ANSI stripping, eval model-availability gate) are correct and well-tested. However, two high-severity security wiring gaps undermine the documented 5-layer defense model and should be fixed before this batch is considered complete.

---

## Validation Performed

- `pytest -x --tb=short -q` → **1561 passed, 1 skipped**
- `ruff check vibe/ tests/` → clean
- `ruff format --check vibe/ tests/` → clean
- No hardcoded secrets found in `vibe/` or `scripts/`

---

## Recent Changes Verdict

The following commits from the last 24 hours are approved as-is:

| Commit | Change | Verdict |
|--------|--------|---------|
| `0527c44` | Bump `actions/checkout` → v5, `actions/setup-python` → v6, install `.[dev,api]` | Correct |
| `88e403e` | Ruff format + lint fixes across `vibe/` and `tests/` | Correct |
| `b404f37` | Fix `QueryLoop.copy()` compactor handling | Correct, regression test added |
| `01370ce` | Skip eval when model endpoint unreachable; fix pytest matcher JSON | Correct |
| `eb36c0b` | Strip ANSI escapes before CLI help assertions | Correct |

---

## HIGH Severity Issues

### H1. Parsed `SecurityConfig` never reaches the security coordinator

**Location:** `vibe/core/query_loop.py:218`

**Issue:**
`SecurityCoordinator(security_config)` is constructed, but no caller in `vibe/` actually passes the parsed config. `QueryLoopFactory` and `vibe/cli/main.py` have zero references to security wiring; `config.py:645` parses the config and drops it.

**Consequences when `security_config=None`:**

| Configured behavior | Actual behavior |
|---------------------|-----------------|
| `security.approval_mode: auto` | Ignored; every `bash`/`write_file`/`delete_file` blocks on interactive stdin prompt |
| `security.fail_closed: true` (default, AGENTS.md §7) | Becomes `getattr(None, "fail_closed", False)` → **fails open** on checkpoint errors |
| SmartApprover LLM risk assessment | Permanently disabled (`llm_client` never passed) |
| CheckpointManager (Layer 5 of 5-layer defense) | Dead code in the query loop |

**Fix:**
Pass the parsed security config through `QueryLoopFactory` → `QueryLoop(security_config=..., checkpoint_manager=...)`; pass `llm_client` into `SecurityCoordinator`; add a wiring test asserting the coordinator receives the real config.

---

### H2. LLM can self-grant shell mode via undeclared tool argument

**Location:** `vibe/tools/bash.py:155`

**Issue:**
`BashTool.execute(command, use_shell=False, **kwargs)` accepts `use_shell` from model tool-call arguments. The tool schema (`bash.py:73-83`) only declares `command`, and `ToolSystem.execute_tool` forwards arbitrary kwargs (`tool_system.py:68`).

In `auto` approval mode there is no human gate (`coordinators.py:404-405`), so the model can switch execution from `create_subprocess_exec` to `create_subprocess_shell` by adding `"use_shell": true`. This bypasses the unquoted-metacharacter guard that `bash.py:15-18` documents as a primary defense.

Even in interactive mode, the approval prompt shows only `command`, not the flag.

**Fix:**
Pop `use_shell` from model-supplied args in `BashTool.execute` (or validate against the declared schema in `ToolSystem.execute_tool`) and let only the security layer set it.

---

## MEDIUM Severity Issues

### M1. Approval→`use_shell` grant broken for JSON-string arguments

**Location:** `vibe/core/coordinators.py:425`

After human approval, the code mutates `tool_args["use_shell"] = True`. But `extract_tool_call_arguments` (`vibe/tools/_utils.py:31-32`) returns a fresh `json.loads()` dict for string args; `ToolExecutor.execute` re-extracts arguments later, discarding the grant. Approved piped commands still fail with "shell mode not enabled". Behavior differs by call format (dict vs JSON string).

**Fix:** Return the grant in `SecurityCheckResult` and have `ToolExecutor` apply it explicitly.

### M2. EvoX strategy sandbox is not a security boundary

**Location:** `vibe/evox/strategy_code.py:82-143`, `docs/EvoX_implementation.md:72`

`EvolvableStrategy.compile()` restricts imports/builtins, but `exec`'d LLM-generated code runs in-process. Classic escapes need no builtins (`"".__class__.__base__.__subclasses__()`), and strategy functions receive live objects (`rng`, `Candidate`s) whose class graphs reach process-wide state.

For an offline research tool this may be acceptable, but the docs overstate it as a "restricted import allowlist".

**Fix:** Document explicitly that strategy code is trusted code, or run validation trials in a subprocess.

### M3. EvoX strategy signal J(S) is degenerate for negative score ranges

**Location:** `vibe/evox/loop.py:157`

`delta * log1p(max(start_score, 0)) / sqrt(W)` → for negative-scored tasks (both built-in string/expression evaluators score ≤ 0), `log1p(0)=0`, so J(S) ≡ 0 regardless of progress. Score-biased parent-strategy selection (`loop.py:202-205`) degenerates to ~uniform.

**Fix:** Shift scores into positive range before applying the formula (e.g., `log1p(max(start - floor, 0))`).

### M4. `QueryLoop.copy()` shares the mutable LLM client

**Location:** `vibe/core/query_loop.py:1275`

`copy.copy(self)` shares `self.llm`; `set_model`/cost-router model switches in a copy mutate the parent's client and vice versa — state bleed across eval cases.

**Fix:** Shallow-copy the client or document sharing explicitly.

### M5. Checkpoint serialization on every state transition

**Location:** `vibe/core/query_loop.py:229-265`

`_checkpoint()` serializes the full message history on each `_set_state` (several per iteration) → O(n²) writes per session, synchronously on the event-loop thread.

**Fix:** Debounce, checkpoint only at iteration boundaries, or offload.

---

## LOW Severity Issues

| ID | Location | Issue |
|----|----------|-------|
| L1 | `query_loop.py:286-297` | Production `run()` imports `unittest.mock` and sniffs `complete_stream` mock internals to decide streaming. Move to test fixtures. |
| L2 | `query_loop.py:462-473` | Streaming usage sums `completion_tokens` across chunks but maxes `prompt_tokens`; cumulative per-chunk usage inflates totals. |
| L3 | `coordinators.py:10`, `coordinators.py:362-370` | Docstring claims run() is "a thin orchestrator (< 40 lines)" — now ~370 lines. `_check_patterns` blocks only `critical` severity, silently passing `high`. |
| L4 | `vibe/evox/strategy.py:140-149`, `strategy.py:74`, `loop.py:263` | `SearchStrategy.copy()` mints new `id`, inflating `strategy_switches`; `_synthesize_code` interpolates instructions into docstring (quotes/newlines break codegen); population `max(...)` recomputed each iteration is O(n²). |
| L5 | `.github/workflows/ci.yml` | `eval` job has no `needs: [unit-test]`; socket check conflates "port open" with "model available". |
| L6 | `docs/ROADMAP.md`, `docs/ARCHITECTURE.md` | Doc drift: claims "50+ eval cases" (actual: 46 YAML); ROADMAP footer says "1424 tests" (actual: 1561). |
| L7 | Repo root | `MagicMock/` and `.vibe/` untracked at root; `.gitignore` covers `~/.vibe/` but not `.vibe/`. |

---

## Alignment: Implementation vs Plans

- **EvoX implementation** matches `docs/EvoX_implementation.md` closely (Algorithm 1 structure, window scoring, strategy DB, mock/LLM generators, CLI). Caveats: J(S) degeneracy (M3) and sandbox-wording (M2).
- **AGENTS.md** (uncommitted edit) is accurate and an improvement (correct 46 eval count, EvoX section).
- **ROADMAP/ARCHITECTURE** lag reality (L6).
- **"5-layer security defense"** is documented prominently but currently operates as ~3.5 layers due to H1.

---

## Recommendation

**REQUEST CHANGES** for the security wiring gaps (H1, H2). Both have cheap, surgical fixes.

Approve the recent CI/test fixes as-is.

---

## Suggested Next Steps

1. Wire `SecurityConfig` through `QueryLoopFactory` → `QueryLoop` → `SecurityCoordinator` (H1).
2. Reject `use_shell` in model-supplied tool args (H2).
3. Fix approval grant persistence for JSON-string args (M1).
4. Update EvoX docs to clarify trusted-code model and fix J(S) score shift (M2, M3).
5. Address doc drift in ROADMAP/ARCHITECTURE (L6).
6. Add `.vibe/` and `MagicMock/` to `.gitignore` (L7).
