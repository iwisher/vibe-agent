# TODO — Repo Cleanup Tracker

> Origin: code review of commits `514c055`..`4f96763` (2026-08-22), fixes landed in
> `d3d0a49`, cleanup plan verified against the tree at `21d8495`.
> Buckets: **Done** → **Open** → **Won't fix (decided)** → **Proposed cleanups**.
> Do not re-raise items listed under "Won't fix" without new evidence.

---

## Done (landed in `d3d0a49` and verified in code)

- Browser SSRF hardening: manual redirect loop with per-hop `is_safe_url` validation,
  `urljoin` for relative redirects, 5-redirect cap (`vibe/tools/browser.py:405-445`);
  IPv6-mapped IPv4 normalized via `ipv4_mapped` + `is_global` allowlist
  (`browser.py:63-67`); `DocumentConverter` cached per tool instance; `mode="static"`
  + `action="click"` now errors instead of silently ignoring the click.
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
  - 2: Duplicate root test files removed (`tests/test_trace_store.py`, `tests/test_session_store.py`).
  - 3: Secret redaction patterns consolidated into `SecretRedactor` (`vibe/tools/security/redaction.py` removed).
  - 4: Standalone SSRF checker removed (`vibe/tools/security/url_safety.py` removed; CGNAT/Alibaba added to `SSRFGuard`).
  - 5: Vector index upgrade shim removed (`vibe/memory/vector_index_upgrade.py` removed).
- [x] Open items 1 & 2 resolved: docstring in `reflection.py` updated; redundant local `import json` in `query_loop.py` removed.

---

## Open

1. [MED] Playwright tier (Tier 2) follows redirects inside Chromium without SSRF
   re-validation — only the static tier got the per-hop loop. Mitigation: intercept
   requests via Playwright routing and validate each target, or document the gap.
2. [LOW] `vibe/tools/security/human_approval.py:358` — the `view` branch prints directly
   from the security worker thread while prompt_toolkit owns the terminal (display-only
   overlap; the re-prompt itself goes through the hook).
3. [LOW] `vibe/cli/main.py:131` — on approval-hook timeout the orphaned `ask()`
   coroutine is never cancelled and keeps blocking on stdin, racing prompt_toolkit.
   Rare (requires the CLI loop stalled >70s).
4. [LOW] `vibe/cli/main.py` — console-mode approval-hook registration sits before its
   `try/finally`; an exception in between leaks the registration for the process
   lifetime.
5. [LOW] `vibe/memory/compaction.py` — writes a `supersedes: <ids>` content line that
   nothing parses; either parse it on read or drop the line (the `superseded` citation
   is the real mechanism).

---

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
