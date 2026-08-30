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

## Open

### Track 1: Evaluation Suite & Benchmark Expansion
- [x] Audit built-in YAML eval cases across all subsystems (`file`, `bash`, `browser`, `memory`, `security`, `tool_system`).
- [x] Synchronize `scripts/validate_eval_tags.py` subsystem and category allowlists with the repository schema.
- [x] Add targeted eval cases in `vibe/evals/builtin/`:
  - `browser_fetch_001.yaml` (Tier 1 static web extraction and content parsing).
  - `browser_ssrf_001.yaml` (SSRF safety check rejecting private and metadata IP targets).
  - `skill_script_001.yaml` (Deterministic script-backed skill execution with variable substitution).
  - `memory_reflection_001.yaml` (Trajectory reflection lesson generation and usage counters).
- [x] Validate eval YAML schema tags with `scripts/validate_eval_tags.py` (50/50 cases pass, 0 violations).
- [ ] Refresh baseline scorecard via `vibe eval update-baseline` after a live eval run
  (50 cases now; requires a reachable model endpoint — unverified numbers are not committed).

### Track 2: EvoX Harness Meta-Evolution Execution (Current Focus)
- [ ] Benchmark `vibe evox run --target harness` across expanded eval suite with `--limit 20`.
- [ ] Verify harness knob search space (`routing_min_confidence`, `max_lessons`, `min_generality`, reflection prompts).
- [ ] Validate regression gate against `docs/baseline_scorecard.json` and verify JSONL provenance export.
- [ ] Log discovered Pareto improvements and document harness optimization results.

### Track 3: Built-in Executable Skills & Swarm Workflow Expansion
- [x] Create new deterministic script-backed skills in `skills/` adhering to the Anthropic Agent Skills / CodeAct pattern:
  - `skills/git-workflow/` (branching, status inspection, commit graph tree, linked worktrees).
  - `skills/code-auditor/` (syntax, security linting, line length checks).
- [x] Validate skill schemas and security with `vibe skill validate` (`skills/git-workflow`, `skills/code-auditor`, `skills/stock-analysis`).
- [ ] Enhance Swarm Multi-Agent orchestrator (`vibe/swarm/`) with dynamic sub-agent role pipelines and trace reporting.

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
