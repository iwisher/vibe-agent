# TODO — Repo Cleanup Tracker

> Origin: code review of commits `514c055`..`4f96763` (2026-08-22), fixes landed in
> `d3d0a49`, cleanup plan verified against the tree at `21d8495`.
> Buckets: **Done** → **Open** → **Won't fix (decided)** → **Proposed cleanups**.
> Do not re-raise items listed under "Won't fix" without new evidence.

---

## Done (executed and verified in code)

- Browser SSRF hardening: manual redirect loop with per-hop `is_safe_url` validation,
  `urljoin` for relative redirects, 5-redirect cap (`vibe/tools/browser.py:405-445`);
  IPv6-mapped IPv4 normalized via `ipv4_mapped` + `is_global` allowlist
  (`browser.py:63-67`); `DocumentConverter` cached per tool instance; `mode="static"`
  + `action="click"` now errors instead of silently ignoring the click.
- Playwright tier (Tier 2) SSRF protection: intercepted all page navigation and subresource
  requests via `page.route("**/*")` validating each URL with `is_safe_url` and aborting
  unsafe targets with `blockedbyclient` (`vibe/tools/browser.py:259-267`).
- Security approval UI hook hygiene:
  - `_map_choice("view")` now skips direct background console printing when a UI hook
    is active (`vibe/tools/security/human_approval.py:358`).
  - Approval hook timeout now cancels the orphaned `ask()` future (`vibe/cli/main.py:136`).
  - Readline interactive mode encloses `set_approval_ui_hook` in a `try...finally` block,
    guaranteeing `reset_approval_ui_hook` on any startup or runtime failure (`vibe/cli/main.py:307-463`).
- Lesson compaction supersedes lineage: structured `supersedes` parameter in `_render_lesson_content`
  and added `_read_supersedes` parser helper (`vibe/memory/reflection.py:110-135`).
- `FetchUrlTool` alias added and registered (`vibe/core/query_loop_factory.py:127-129`).
- `_pivotal_turn` cleared after trajectory reflection consumes it
  (`vibe/core/query_loop.py:1576-1577`) — iteration-index semantics kept (see Decisions).
- SkillMaker approval gate unified: single `CLIApprovalGate` shared by `SkillInstaller`
  and the maker pipeline (`query_loop_factory.py:269-274`); blocking `input()` offloaded
  via `asyncio.to_thread` (`vibe/harness/skills/maker.py:461`).
- `SkillRunner`: `${VAR:-default}` defaults now shlex-quoted in `quote=True` mode
  (`vibe/tools/skill_runner.py:263`).
- `vibe memory wiki index/compact` now use the configured `index_path` via
  `_get_pageindex()` instead of a hardcoded `~/.vibe/memory/index.json`.
- Unreferenced 245 KB screenshot deleted from `docs/assets/`.
- [x] Cleanups 1-5 executed and verified:
  - 1: Dual dashboard backend consolidated onto `server.py` (`api.py`/`data.py`/`test_api.py` removed; `__init__.py` re-exported).
  - 2: Duplicate root test files merged into `tests/harness/memory/test_session_store.py` (13 CRUD tests) and `tests/harness/memory/test_trace_store.py` (4 similarity tests); root copies removed.
  - 3: Secret redaction patterns consolidated into `SecretRedactor`; tests mirrored under `tests/harness/security/test_redactor.py` (`vibe/tools/security/redaction.py` removed).
  - 4: Standalone SSRF checker removed (`vibe/tools/security/url_safety.py` removed; CGNAT/Alibaba added to `SSRFGuard`).
  - 5: Vector index upgrade shim removed (`vibe/memory/vector_index_upgrade.py` removed).
- [x] Repository polish: `AGENTS.md` tree updated to match reality; `.tmp/` scratch files untracked from git; stray `MagicMock/` removed; `reflection.py` docstring updated; redundant local `import json` removed.
- [x] Evaluation suite synchronization & expansion (Track 1):
  - Synchronized `scripts/validate_eval_tags.py` valid subsystem and category allowlists (50/50 cases pass validation, 0 violations).
  - Added 4 built-in YAML eval cases in `vibe/evals/builtin/` (`browser_fetch_001`, `browser_ssrf_001`, `skill_script_001`, `memory_reflection_001`).
  - Preserved authentic baseline scorecard in `docs/baseline_scorecard.json` (47 verified cases; updateable via `vibe eval update-baseline` upon live endpoint run).
- [x] Deterministic Agent Skills (Track 3):
  - Created `skills/git-workflow/` (branching, status inspection, commit graph tree, linked worktrees).
  - Created `skills/code-auditor/` (syntax and line length checks).
  - Verified with `vibe skill validate` and linted with `ruff check skills/`.
- [x] Test isolation & log hygiene:
  - Mocked `sys.modules` for Playwright in `tests/tools/test_browser.py` to allow clean test execution without optional browser binaries.
  - Untracked `logs/session_test-ses.log` from git index to prevent test runs from dirtying working tree.
- [x] Multi-Agent Red-Team Scaffolding & Defense Remediations (Track 4):
  - Created `vibe/redteam/` harness: corpus loader with schema validation, victim isolation in tmpdir with shadow-branch cleanup, deterministic oracles (S1–S5, S7), orchestrator over asyncio + swarm `EventBroker`, and report generators.
  - Authored 6 bundled YAML corpus suites in `vibe/redteam/corpus/` (31 attack entries) validated via `scripts/validate_redteam_corpus.py`.
  - Remediated S7 MCP Bridge HTTP SSRF gap with `await SSRFGuard.is_safe_async(url)` in `MCPBridge._invoke_http` (explicit `follow_redirects=False`; per-server `allow_private` opt-out for local MCP servers).
  - Remediated S4 SmartApprover prompt injection with `UNTRUSTED_*` fence delimiters plus fence-marker munging (anti-spoofing) in `SmartApprover._llm_risk_assessment`.
  - Added critical `base64-pipe-sh` pattern to `BUILTIN_PATTERNS` in `vibe/tools/security/patterns.py` (found by the red team's first run).
  - Added 61 unit/integration tests in `tests/redteam/` (1,910 total repo tests pass).
- [x] Tier B compromised-model scenarios (Track 4): 7 scripted hostile-model scenarios
  (`vibe/redteam/tier_b.py`) run through a real QueryLoop + SecurityCoordinator inside the
  victim jail (safe_root + CWD + HOME all redirected); each asserts containment at the
  expected layer plus zero side effects. `tb-fooled-approver` documents the known residual:
  an evasive payload + fooled Layer 4 executes, but lands inside the jail only.
- [x] Tier 3 Long-Horizon Challenged Agent Tasks (Track 4): 10 benchmark scenarios
  (`vibe/redteam/tier_3.py`) covering top failure modes in autonomous agent runtimes (cross-module
  refactoring, database migration, supply-chain audit, workflow rollback, log anomaly clustering,
  workspace checksums, skill synthesis, incident snapshot).
- [x] Built-in TaskVerifierTool & Deterministic Skills (Track 3 & 4):
  - Created `vibe/tools/task_verifier.py` (`TaskVerifierTool`) for AST syntax/import inspection,
    file checksum validation, SQLite schema/row invariants, and error signature log clustering.
  - Created `skills/refactor-verifier/` (AST & cross-import contract validation).
  - Created `skills/db-migrator/` (atomic migration with snapshot backup & rollback).
  - Created `skills/log-analyst/` (noisy log triage & error signature clustering).
  - Created `skills/dependency-auditor/` (supply-chain and unencrypted dependency scanning).
  - Verified with `vibe skill validate` and 100% test coverage.
- [x] Tier C live gating (Track 4): `--live --provider kimi|gemini` on
  `scripts/run_redteam.py` with endpoint-specific config (`vibe/redteam/live.py`);
  verified against Gemini (`gemini-flash-latest`), confirming model refusal and full defense containment.
- [x] Findings report: `scripts/run_redteam.py` writes `docs/redteam_report.{json,md}`
  (Tier A 30/30, Tier B 7/7, Tier 3 10/10 at last run).

## Open

### Track 2: EvoX Harness Meta-Evolution Execution (Current Focus)
- [ ] Benchmark `vibe evox run --target harness` across expanded eval suite with `--limit 20`.
- [ ] Verify harness knob search space (`routing_min_confidence`, `max_lessons`, `min_generality`, reflection prompts).
- [ ] Validate regression gate against `docs/baseline_scorecard.json` and verify JSONL provenance export.
- [ ] Log discovered Pareto improvements and document harness optimization results.

### Track 3: Swarm Workflow Expansion
- [ ] Enhance Swarm Multi-Agent orchestrator (`vibe/swarm/`) with dynamic sub-agent role pipelines and trace reporting.

### Track 4: Multi-Agent Red-Team (Complete)
- [x] Tier A component attack matrix (S1–S5, S7) with corpus, oracles, orchestrator.
- [x] Remediation wave 1: S7 MCP SSRF gate, S4 approver fencing, base64-pipe-sh pattern.
- [x] Tier B scripted-compromised-LLM scenarios (7, incl. strict-mode + fooled-approver).
- [x] Tier 3 long-horizon challenged agent tasks (10 tasks across software engineering, db, security, reliability).
- [x] Tier C `--live` flag wired and verified against Gemini (`gemini-flash-latest`), confirming model refusal and full defense containment.

## Won't fix — decided 2026-08-23 (intentionally kept as-is)

1. **`_pivotal_turn` stays a loop-iteration index.** The test suite
   (`tests/core/test_query_loop_pivotal_retry.py`) explicitly validates this contract
   (`assert loop._pivotal_turn == 2`). Changing to transcript message index would break
   the public contract. The only real defect was the cross-run leak — fixed by clearing.
2. **`get_session_cost()` kept.** Valid public optional getter on `CostRouter` and
   `QueryLoop`; returns the accumulated cost when tracking is configured, `None`
   otherwise. Removing it is churn with no practical benefit.
3. **No history-list caching in `_history_backward()`.** Rebuilding the list from
   prompt_toolkit's buffer costs <50 µs at realistic history sizes; a persistent cache
   adds invalidation bugs (mid-session appends/clears) for zero perceptible gain.
4. **No socket-level DNS pinning against rebinding TOCTOU.** Hostname/IP validation on
   the initial request plus every redirect hop provides comprehensive SSRF defense;
   custom socket-level pinning in httpx/Playwright is brittle, breaks TLS SNI hostname
   validation, and complicates proxy support.
5. **Pivotal retries do not consume the iteration budget.** The guided retry is a
   bounded micro-recovery (capped at 1 per failure signature via
   `max_pivotal_retries = 1`); counting it as a full planner iteration would push
   complex tasks into premature INCOMPLETE on transient parameter corrections.
6. **EvoX `iter_points` / `to_dict` / `from_dict` kept.** Standard serialization API
   used by meta-evolution evaluation and regression tests; deletion saves nothing at
   runtime and reduces testability.

---

## Historical review notes (archive — context for the above)

Condensed from the original per-commit review; kept for rationale, not action.

- `514c055` (skill scripts, memory wiring, reflection, rendering): verified real fixes
  (TraceStore factory kwarg bug, PageIndex no-op indexing, `tripartite` vs `memory`
  config-attr bug, parser dropping `[[variables]]`). Note: flipped
  `memory.enabled`/`auto_extract` defaults to `True` — post-session LLM extraction now
  fires out of the box; intentional and documented.
- `f4af482` (lesson gate, pivotal retry): README says the retry retries "just that
  call" but the code executes all tool calls the model returns on retry — doc wording,
  low impact. Plan-doc edits for workstreams B/D belonged to `8a40bdb`'s scope.
- `8a40bdb` (lesson lifecycle, EvoX harness target, RLM relabeling): large but fully
  wired; +82 real tests.
- `3007d9a` (approval overlap fix): correct hook-contract architecture, legacy path
  preserved, 27 tests; leftovers tracked in Open 4–6.
- `334909f` (history fix + dashboard cleanup): verified correct; research-paper removal
  left no dangling references.
- `ad5345b` / `bcaea60` / `4adc2d2` (browser tool + spec/plan): feature landed with
  real tests; security gaps fixed in `d3d0a49`.
- `4f96763` / `1fe0f3d` (cleanup + study log): `.pyc`/stub/scratch purges verified
  clean; the stray screenshot slipped through and was deleted in `d3d0a49`.
