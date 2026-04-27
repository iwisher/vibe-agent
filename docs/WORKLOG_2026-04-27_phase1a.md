# Worklog: Phase 1a Tripartite Memory System + Test Cleanup

**Date:** 2026-04-27  
**Session Duration:** ~2 hours  
**Branch:** `main`  
**Commits:**
- `0e035db` — feat: Phase 1a Tripartite Memory System (33 files, 6,647 insertions)
- `dd7ae6d` — fix: resolve all pre-existing test failures (27 → 0)
- `f815469` — chore: remove stray =3.8.0 file

---

## Part 1 — Phase 1a Tripartite Memory System

Implemented the complete Phase 1a foundation for the Tripartite Memory System as described in `docs/TRIPARTITE_MEMORY_DESIGN_v4.md` and `docs/IMPLEMENTATION_PLAN_Phase1a.md`.

### Wave 1 — Foundation Modules

#### `vibe/core/config.py`
Added four new Pydantic config models nested under `VibeConfig.memory`:
- **`WikiConfig`** — `auto_extract`, `base_path`, `novelty_threshold`, `confidence_threshold`, `default_ttl_days`
- **`PageIndexConfig`** — `index_path`, `max_nodes_per_index`, `token_threshold`, `routing_timeout_seconds`
- **`RLMConfig`** — `enabled` (Phase 2 placeholder)
- **`TripartiteMemoryConfig`** — top-level gate (`enabled=False` by default, zero behavior change)

Enabled via `VIBE_MEMORY='{"enabled": true}'` environment variable.

#### `vibe/memory/models.py` (new)
Shared data models:
- **`WikiPage`** — dataclass with `id`, `slug`, `title`, `content`, `tags`, `status` (`draft`/`verified`), `citations`, `path`, date fields, TTL
- **`IndexNode`** — tree node for PageIndex with `to_dict()` / `from_dict()` serialization

#### `vibe/memory/wiki.py` (new)
`LLMWiki` — file-based wiki storage layer:
- YAML frontmatter + Markdown body format
- UUID page identity, slug generation (`_make_slug`, `_content_hash`)
- `AsyncFileLock` for concurrency safety (filelock ≥ 3.8)
- Locking hierarchy: index lock → page lock (sorted by path, deadlock-free)
- Full CRUD: `create_page`, `get_page`, `get_page_by_slug`, `update_page`, `delete_page`
- Listing + filtering: `list_pages(tag=, status=)`
- BM25-style search: `search_pages(query, limit)`
- Backlink reverse index: `get_backlinks(page_id)`
- Quality gate: auto-promotes `draft` → `verified` when ≥ 2 citations from distinct sessions
- Draft expiration: `expire_drafts(cutoff_days)` — never touches `verified` pages
- Closable protocol: `close()` (idempotent)

#### `vibe/memory/pageindex.py` (new)
`PageIndex` — JSON-based routing tree:
- Persistent `index.json` with `wiki_index` root key
- Node CRUD: `add_node`, `remove_node`, `update_node`, `_find_node`
- Keyword routing: `async route(query)` returns `IndexNode` list with `.confidence` scores
- 2-second timeout guard — never blocks planner
- Deterministic tag-based partitioning: auto-reorganises leaf nodes into category subtrees when count exceeds `max_nodes_per_index`
- `rebuild(wiki, incremental=False)` — full index rebuild from wiki pages

#### `vibe/memory/shared_db.py` (new)
`SharedMemoryDB` — unified SQLite storage:
- Tables: `sessions`, `evals`, `wiki_chunks` (FTS5 with porter tokenizer), `chunk_meta` (content-hash skip), `_telemetry`, `_schema_version`
- `MigrationManager` — explicit versioned migrations, no silent auto-migration
- Migration paths from legacy `traces.db` / `evals.db`
- `sync_wiki_page(page)` — content-hash-gated FTS5 re-indexing
- `delete_wiki_page(page_id)` — removes from FTS5 + chunk_meta
- `record_telemetry(event_type, ...)` / `query_telemetry_summary(days)`

#### `vibe/memory/flash_client.py` (new)
`FlashLLMClient` — cheap-model quality gate client:
- Routes to configurable model (default: `qwen3:1.7b` via Ollama)
- Used for contradiction detection in future quality gates
- Graceful fallback: logs warning and returns `None` if unreachable

#### `vibe/memory/telemetry.py` (new)
`TelemetryCollector` — Phase 2 scaling metrics:
- `record_compaction(session_id, content_size, token_count, strategy, was_compacted)`
- `record_session(session_id, duration_seconds, total_chars, state)`
- Writes to `SharedMemoryDB._telemetry` table

#### `vibe/memory/rate_limiter.py` (new)
`TokenBucket` — Phase 2 RLM rate limiting placeholder.

### Wave 2 — Integration

#### `vibe/harness/planner.py`
- Added `wiki_hint: str = ""` field to `PlanRequest`
- Added keyword-only `pageindex` param to `HybridPlanner.__init__` (preserves positional compat)
- `wiki_hint` injected into system prompt append when non-empty

#### `vibe/core/query_loop.py`
- Added `wiki`, `pageindex`, `telemetry` constructor params (all optional, zero-change when absent)
- Async `pageindex.route(query)` called before planner with 2s timeout guard
- `_wiki_extract_task: asyncio.Task | None` field for deferred background extraction
- Closable protocol: `close()` cancels `_wiki_extract_task`, calls `wiki.close()`, `llm_client.close()`
- Session telemetry recorded on each `run()` completion

#### `vibe/core/query_loop_factory.py`
- `_create_trace_store()` wired before tripartite components
- `_create_tripartite(mem_cfg)` — conditionally creates `LLMWiki`, `PageIndex`, `TelemetryCollector` when `memory.enabled=True`
- Zero-change when disabled

#### `vibe/core/context_compactor.py`
- Added `telemetry_collector` optional param
- Compaction events recorded to telemetry on each compaction

### Wave 3 — CLI & Tests

#### `vibe/cli/main.py`
New `vibe memory wiki` subcommand group:
- `vibe memory wiki list [--tag] [--status]` — table of pages
- `vibe memory wiki search <query> [--limit]` — BM25 search with snippets
- `vibe memory wiki show <id|slug>` — rich Panel display
- `vibe memory wiki create --title <t> [--tags] [--content]` — creates page, opens `$EDITOR` if no content
- `vibe memory wiki edit <id|slug>` — opens `$EDITOR` for content update
- `vibe memory wiki index rebuild` — full `PageIndex` rebuild from wiki
- `vibe memory wiki expire [--days]` — expire old draft pages

#### `tests/memory/` (new package, 4 files, 74 tests)
- **`test_wiki.py`** (36 tests) — CRUD, YAML frontmatter, quality gates, backlinks, expiration, concurrency stress (10 parallel writers, 0 corruption), close idempotency
- **`test_pageindex.py`** (22 tests) — load/save, node CRUD, keyword routing, partitioning determinism, `IndexNode` serialization
- **`test_shared_db.py`** (16 tests) — initialization, FTS5 wiki chunks, skip-reindex, telemetry, `MigrationManager`
- **`test_integration.py`** — end-to-end wiki+search, factory wiring order, QueryLoop tripartite params, `close()` task cancellation, env override, config defaults

**Result:** 74/74 new tests passing, 0 regressions.

---

## Part 2 — Pre-Existing Test Failure Cleanup

Fixed all 27 pre-existing test failures (+ 3 collection errors) that existed in the repo before this session.

### Root Cause 1: Missing Dependencies

`numpy` and `jinja2` were used by production code but not listed as dependencies.

| Package | Production Usage | Test Impact |
|---|---|---|
| `numpy` | `cosine_similarity()` in `embeddings.py` + `HybridPlanner._cosine_similarity()` | Returned `0.0` for all similarities; embedding tier never activated |
| `jinja2` | `SkillExecutor._render_template()` | `{{ }}` templates returned as literal strings |

**Fix:** `uv add numpy jinja2` + added to `pyproject.toml` dependencies.

**Tests fixed:** 5 embedding/planner tests + 8 executor tests.

### Root Cause 2: Config API Gap

Tests used a legacy file-based config API that didn't exist in the new pydantic-settings `VibeConfig`. Specifically:
- `VibeConfig.load(path=..., auto_create=True/False)` — didn't accept any arguments
- `FallbackConfig` — class didn't exist
- `FileSafetyConfig`, `EnvSanitizationConfig`, `SandboxConfig`, `AuditConfig` — classes didn't exist
- `SecurityConfig` was a thin stub missing `approval_mode`, `fail_closed`, etc.
- `_parse_bool`, `_parse_float`, `_parse_int`, `_parse_list` — helper functions missing
- `_parse_providers` — factory function missing
- `VibeConfig.get_fallback_chain()`, `get_security_config()`, `get_default_provider()`, `set_resolved_model()` — methods missing
- `VibeConfig.fallback`, `providers`, `models`, `resolved_model` — fields missing
- `EvalConfig.scorecard_dir` — field missing
- `FallbackConfig.health_check_timeout`, `max_retries` — fields used by `health_check.py` but missing

**Fix:** Full rewrite of `vibe/core/config.py` adding all legacy API while preserving the pydantic-settings `VibeConfig()` interface.

Key design decisions:
- `VibeConfig.load()` uses `model_construct` to bypass pydantic-settings env reading (which uses `VIBE_*` prefix) and instead applies its own bespoke env var logic (`VIBE_MODEL`, `VIBE_CB_THRESHOLD`, etc.)
- All new classes (`FallbackConfig`, `FileSafetyConfig`, etc.) are plain `BaseModel` (not `BaseSettings`) so they don't read env vars on their own
- `SecurityConfig.approval_mode` uses a `@field_validator` for exact error message: `"approval_mode must be one of ..."`
- `AuditConfig.max_events` uses a `@field_validator` (not `ge=1`) so the error message matches `"max_events must be >= 1"` exactly
- `_parse_providers` builds a `ProviderRegistry` from a raw YAML `providers` dict, with backward-compat synthesis of a `"default"` provider from `LLMConfig` when no providers section exists
- `log_level` and `debug` are carried through from the raw YAML dict in `load()`

**Tests fixed:** 3 collection errors + 17 test failures across `test_config.py`, `test_config_security.py`, `test_config_providers.py`, `test_health_check.py`, `test_health_check_providers.py`, `test_multi_provider_benchmark.py`, `test_query_loop_factory_adapter.py`.

### Final Test Results

```
852 passed, 1 skipped, 0 failed  (was: 27 failures + 3 collection errors)
```

The 1 skip is a pre-existing intentional skip (not a failure).

---

## Dependencies Added

| Package | Version | Reason |
|---|---|---|
| `numpy` | `≥2.0` | `cosine_similarity`, `HybridPlanner` embedding tier |
| `jinja2` | `≥3.0` | `SkillExecutor` template rendering |
| `filelock` | `≥3.8.0` | `AsyncFileLock` in `LLMWiki` |
| `pydantic-settings` | `≥2.0.0` | `VibeConfig(BaseSettings)` env var loading |

---

## Files Changed Summary

### New Files
```
vibe/memory/__init__.py
vibe/memory/models.py
vibe/memory/wiki.py
vibe/memory/pageindex.py
vibe/memory/shared_db.py
vibe/memory/flash_client.py
vibe/memory/telemetry.py
vibe/memory/rate_limiter.py
tests/memory/__init__.py
tests/memory/test_wiki.py
tests/memory/test_pageindex.py
tests/memory/test_shared_db.py
tests/memory/test_integration.py
```

### Modified Files
```
vibe/core/config.py          — Full rewrite: legacy API + tripartite config
vibe/core/query_loop.py      — wiki/pageindex/telemetry wiring, Closable protocol
vibe/core/query_loop_factory.py — _create_tripartite() factory
vibe/core/context_compactor.py  — telemetry hook
vibe/harness/planner.py      — wiki_hint in PlanRequest, pageindex param
vibe/cli/main.py             — vibe memory wiki subcommands
pyproject.toml               — numpy, jinja2, filelock, pydantic-settings deps
```

---

## Next Steps (Phase 1b / Phase 2)

1. **Phase 1b — Async Extraction:** Implement `_wiki_extract_task` background loop in `QueryLoop` to extract knowledge from completed conversations without blocking user interaction
2. **Phase 2 — RLM Scaling:** Use `_telemetry` data to trigger `RLM` (Recursive Language Model) training when compaction/session metrics cross thresholds
3. **Quality Gates:** Wire `FlashLLMClient` to contradiction detection in `update_page()`
4. **CLI Polish:** Add `vibe memory status` command showing wiki page count, index size, telemetry summary
