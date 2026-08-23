# TODO — Code Review Findings (last 10 commits)

> Source: code review of commits `514c055`..`4f96763` (2026-08-22).
> Items marked **[HEAD]** were verified still present at the time of review.

## Priority actions

1. [x] Fix browser SSRF redirect + IPv6-mapped bypass (`ad5345b`). Resolved: normalized IPv6-mapped IPv4 in SSRFGuard, added manual redirect loop with SSRF check per hop, cached DocumentConverter, disallowed static click.
2. [x] Fix `_pivotal_turn` index units and clear it after reflection (`f4af482`). Resolved: set to transcript message index (`len(self.messages) - 1`), cleared upon consumption in reflection task.
3. [x] Unify the SkillMaker double approval gate (`8a40bdb`). Resolved: passed unified CLIApprovalGate to both SkillInstaller and SkillMakerPipeline; wrapped approve call in `asyncio.to_thread`.
4. [x] Delete the stray screenshot `docs/assets/Screenshot 2026-07-19 at 11.04.42 AM.png`. Resolved: deleted.
5. [x] Correct the README `browse` / `fetch_url` alias claim (`4f96763`). Resolved: added `FetchUrlTool` alias in `vibe/tools/browser.py` and registered in factory.

---

## `4f96763` — docs(cleanup): purge dead stubs, tracked pyc files

- [LOW] README.md claims the browser tool is registered as "`browse` / `fetch_url`", but only `browse` exists (`vibe/tools/browser.py`); no `fetch_url` alias anywhere. Fix doc or register the alias. **[HEAD]**
- [LOW] The cleanup missed the unreferenced screenshot from `1fe0f3d`.
- Necessity: NEEDED overall — `.pyc` purge is covered by gitignore, `vibe/api` stub removal verified clean (no imports), AGENTS.md tree matches reality.

## `ad5345b` — feat(tools): adaptive dual-tier browser tool

- [MED-HIGH] SSRF bypass via redirects: `vibe/tools/browser.py` uses `httpx.AsyncClient(follow_redirects=True)` but `is_safe_url()` validates only the initial URL. A 302 to `http://169.254.169.254/...` is followed unchecked. Fix: disable auto-redirects and validate each `Location`, or validate per-request via an httpx event hook. Playwright tier has the same issue. **[HEAD]**
- [MED] IPv6-mapped IPv4 bypass: `::ffff:127.0.0.1` parses as `IPv6Address` and never matches the IPv4 forbidden networks. Fix: normalize via `ip.ipv4_mapped`, or use an allowlist check like `ip.is_global`. **[HEAD]**
- [LOW] DNS rebinding TOCTOU: validate-then-fetch resolves DNS twice; attacker DNS can answer differently at connect time. At minimum, document the limitation.
- [LOW] Blocking `socket.getaddrinfo` on the event-loop hot path — wrap in `asyncio.to_thread`.
- [LOW] `DocumentConverter()` constructed per request — potentially expensive; cache one instance.
- [LOW] `mode="static"` + `action="click"` silently ignores the click and returns a static read — should error.
- Necessity: NEEDED (feature + tests are real).

## `bcaea60` / `4adc2d2` — browser tool plan & design spec

- Necessity: content NEEDED; QUESTIONABLE packaging — the two docs commits could be one.

## `334909f` — fix(cli): history navigation regression + dashboard cleanup

- Verified good: history recall state machine correct, `FileHistory` wired into TUI input buffer, readline-clobbering guard correct, research-paper removal thorough (no dangling refs in server/JS/CSS/tests).
- [LOW] `_history_backward` rebuilds the full history list on every Up keypress (fine at realistic sizes).
- Necessity: all changes NEEDED; QUESTIONABLE bundling — three logical changes (history fix, `highlight=False` tweak, research-paper removal) in one commit.

## `3007d9a` — fix(cli): approval prompt overlap

- Verified good: hook contract + `asyncio.to_thread` offload is the right architecture; legacy path preserved; 27 real tests.
- [LOW] `vibe/tools/security/human_approval.py:358` — the `view` branch prints directly from the worker thread while prompt_toolkit owns the terminal (same overlap class, display-only). **[HEAD]**
- [LOW] `vibe/cli/main.py:131` — on hook timeout the orphaned `ask()` coroutine is never cancelled and keeps blocking on stdin, racing prompt_toolkit. **[HEAD]**
- [LOW] Console-mode hook registration sits before its `try/finally`; an exception in between leaks the registration.
- Necessity: NEEDED, no dead code, nothing unrelated.

## `8a40bdb` — feat: lesson lifecycle, EvoX harness target, RLM relabeling

- [MED] `vibe/core/query_loop_factory.py:266-269` — double approval gate with conflicting policies: maker gets `CLIApprovalGate` but `SkillInstaller` keeps its default `AutoRejectGate`; after user approval, any validation warning auto-rejects and silently overrides the user's yes. Latent (SkillMaker ships disabled). **[HEAD]**
- [MED] `vibe/harness/skills/approval.py:44` — blocking `input()` runs inside a background task spawned from the query loop, freezing the event loop. The analogous `HumanApprover` issue was fixed in `3007d9a`; this gate was missed. **[HEAD]**
- [LOW] `vibe/cli/main.py` wiki-compact hardcodes `~/.vibe/memory/index.json` instead of reading the config override — desyncs if the user overrides `memory.pageindex.index_path`. **[HEAD]**
- Necessity: NEEDED except `iter_points` / `to_dict` / `from_dict` in `vibe/evox/harness_target.py` (UNNEEDED — test-only speculative serialization API) and the `supersedes: <ids>` content line in `vibe/memory/compaction.py` (UNNEEDED — nothing parses it; the `superseded` citation is the real mechanism).

## `f4af482` — feat: lesson quality gate + usage feedback, pivotal retry

- [MED] `vibe/core/query_loop.py:1154` vs `vibe/memory/reflection.py:217` — unit mismatch: `_pivotal_turn` stores the loop *iteration* index, but the reflection prompt presents it as the *message* index in the transcript; after any tool call these diverge, so the reflection anchor points at the wrong line. **[HEAD]**
- [MED] `vibe/core/query_loop.py:366` — `_pivotal_turn` is never cleared after reflection consumes it; in a multi-run session it leaks into the next run's reflection prompt with a stale index. **[HEAD]**
- [LOW] README claims pivotal retry retries "just that call" — the code executes *all* tool calls the model returns on retry.
- [LOW] The retry LLM call never consumes iteration budget — invisible to adaptive-budget accounting.
- Necessity: NEEDED except the plan-doc edits for workstreams B/D (QUESTIONABLE — design notes for work that landed in `8a40bdb`, unrelated to this commit's A+C scope) and a redundant local `import json` left in `query_loop.py` after the module-level import was added (UNNEEDED).

## `1fe0f3d` — docs: experience-learning study log and plan

- [MED] `docs/assets/Screenshot 2026-07-19 at 11.04.42 AM.png` — UNNEEDED: 245 KB unreferenced personal screenshot (U+202F in filename), never mentioned in the commit message, still tracked at HEAD. Remove. **[HEAD]**
- Necessity: the plan markdown is NEEDED — its claims were accurate and were implemented verbatim in later commits.

## `514c055` — feat: skill scripts, memory wiring, trajectory reflection, CLI rendering

- Verified real fixes: `TraceStore` factory kwarg bug (store always `None`), `PageIndex.add_page` no-op indexing, `tripartite` vs `memory` config-attr bug, parser dropping `[[variables]]`.
- [LOW] `vibe/tools/skill_runner.py` — `${VAR:-default}` defaults are not shlex-quoted even in `quote=True` mode; a default with spaces splits into multiple argv tokens. **[HEAD]**
- [LOW] Behavior change bundled in: `memory.enabled` / `auto_extract` flipped to `True` by default (`vibe/core/config.py:447,509`) — post-session LLM extraction now fires out of the box. Documented, but smuggled into a feature commit.
- [LOW] Shipped without `highlight=False` in `safe_print_chunk` (fixed next commit, `334909f`).
- Necessity: NEEDED except `get_session_cost()` + its 4 call sites (UNNEEDED in practice — `CostRouter` is constructed without `spend_tracker`, so it can never return a value) and the cosmetic Rich approval-banner rewrite in `human_approval.py` (QUESTIONABLE — loosely tied to the commit theme).

---

## Repo-Wide Cleanup & Consolidation Plan (Critique & Actions)

### 1. Dual Dashboard Backend Consolidation
- **Status**: Ready for execution.
- **Context**: `vibe/dashboard/server.py` (~984 lines) is the production FastAPI + WebSocket server used by `vibe dashboard start`. `vibe/dashboard/api.py` and `vibe/dashboard/data.py` (~412 lines) duplicate dataclasses and provide an unreferenced secondary API.
- **Critique & Safety**:
  - `vibe/dashboard/__init__.py` currently imports `create_app` from `api.py`. Must be updated to export `app` from `server.py`.
  - `tests/test_dashboard_api.py` already thoroughly tests `server.py`. `tests/dashboard/test_api.py` tests only the dead `api.py`.
- **Action**:
  - Delete `vibe/dashboard/api.py` and `vibe/dashboard/data.py`.
  - Delete `tests/dashboard/test_api.py`.
  - Update `vibe/dashboard/__init__.py` to export `app` from `server.py`.

### 2. Duplicate Root Test Files Pruning
- **Status**: Ready for execution.
- **Context**: Root test files `tests/test_trace_store.py` (303 lines) and `tests/test_session_store.py` (220 lines) are legacy duplicates of `tests/harness/memory/test_trace_store.py` (422 lines) and `tests/harness/memory/test_session_store.py` (363 lines).
- **Critique & Safety**:
  - Diff verification confirms `tests/harness/memory/` test files are strict supersets with newer tests (`TestSimilarSessionsSuccessFilter`, `TestGetSessionSnippet`, TTL cleanup).
  - Deleting root duplicates eliminates double execution in CI and conforms to the mirrored package layout.
- **Action**:
  - Delete `tests/test_trace_store.py` and `tests/test_session_store.py`.

### 3. Secret Redaction Pattern Consolidation
- **Status**: Ready for execution.
- **Context**: `vibe/harness/security/redactor.py` (`SecretRedactor`) is active in runtime (`session_store.py`, `trace_store.py`), but only has 9 regex patterns. `vibe/tools/security/redaction.py` has 40+ rich patterns (AWS, Slack, GitHub, JWT, Stripe, SSH) but is disconnected from runtime.
- **Critique & Safety**:
  - Rather than blindly deleting `redaction.py` (which would throw away useful security patterns), migrate the comprehensive 40+ regex patterns into `vibe/harness/security/redactor.py`.
  - This strengthens secret redaction across all stored session checkpoints and traces.
- **Action**:
  - Migrate patterns from `vibe/tools/security/redaction.py` into `vibe/harness/security/redactor.py`.
  - Delete disconnected `vibe/tools/security/redaction.py` and its test once consolidated.

### 4. Standalone SSRF / URL Safety Checker Consolidation
- **Status**: Ready for execution.
- **Context**: `SSRFGuard` in `vibe/tools/browser.py` is the hardened, actively tested SSRF layer. `vibe/tools/security/url_safety.py` (`URLSafetyChecker`) is a disconnected utility only used in its own test.
- **Critique & Safety**:
  - `SSRFGuard` already handles DNS resolution, IPv6-mapped addresses, private/loopback CIDRs, and redirect chains.
  - Adding any remaining cloud metadata ranges (e.g. `100.100.100.200` Alibaba metadata) to `SSRFGuard` ensures zero coverage loss.
- **Action**:
  - Ensure `SSRFGuard` covers all cloud metadata IPs, then remove `vibe/tools/security/url_safety.py` and `tests/tools/security/test_url_safety.py`.

### 5. Vector Index Upgrade Shim Pruning
- **Status**: Ready for execution.
- **Context**: `vibe/memory/vector_index_upgrade.py` (`UpgradedVectorIndex`) was a prototype shim before `get_vector_index()` in `vibe/memory/vector_index.py` was introduced.
- **Critique & Safety**:
  - `PageIndex`, `wiki.py`, and `query_loop_factory.py` all use `vibe/memory/vector_index.py`.
- **Action**:
  - Delete `vibe/memory/vector_index_upgrade.py` and `tests/memory/test_vector_index_upgrade.py`.
