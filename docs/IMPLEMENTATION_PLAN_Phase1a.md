# Tripartite Memory System — Phase 1a Implementation Plan

**Based on:** `docs/TRIPARTITE_MEMORY_DESIGN_v4.md`  
**Date:** 2026-04-26  
**Status:** Planning Phase — awaiting approval before implementation

---

## 1. Architecture Overview

The Tripartite Memory System adds three layers to the existing vibe-agent:

```
┌─────────────────────────────────────────────────────────────┐
│  CLI (vibe memory wiki *)                                    │
├─────────────────────────────────────────────────────────────┤
│  QueryLoop.run() — async wiki retrieval before planner       │
│  ├── PageIndex.route(query) → wiki_hint                      │
│  └── HybridPlanner.plan(PlanRequest(wiki_hint=...))         │
├─────────────────────────────────────────────────────────────┤
│  LLMWiki — CRUD, YAML frontmatter, AsyncFileLock             │
│  PageIndex — JSON tree, deterministic partitioning           │
│  SharedMemoryDB — memory.db with FTS5, schema versioning     │
├─────────────────────────────────────────────────────────────┤
│  Existing (unchanged): TraceStore, EvalStore, Compactor     │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Module Breakdown & File Mapping

### 2.1 New Files to Create

| File | Module | Description | Lines Est. |
|------|--------|-------------|------------|
| `vibe/memory/__init__.py` | Package init | Unified exports | 20 |
| `vibe/memory/wiki.py` | LLMWiki | CRUD, YAML frontmatter, UUID, AsyncFileLock, quality gates | ~350 |
| `vibe/memory/pageindex.py` | PageIndex | JSON tree, routing, deterministic partitioning | ~300 |
| `vibe/memory/shared_db.py` | SharedMemoryDB | SQLite consolidation, schema versioning, MigrationManager | ~250 |
| `vibe/memory/models.py` | Data models | WikiPage, IndexNode, Pydantic models | ~100 |
| `vibe/memory/rate_limiter.py` | TokenBucket | For future RLM use (placeholder) | ~50 |
| `vibe/memory/flash_client.py` | FlashLLMClient | Cheap-model routing contract | ~80 |
| `vibe/memory/telemetry.py` | TelemetryCollector | ContextCompactor/QueryLoop metrics | ~100 |
| `tests/memory/test_wiki.py` | Unit tests | CRUD, locking, expiration | ~200 |
| `tests/memory/test_pageindex.py` | Unit tests | Routing, partitioning | ~150 |
| `tests/memory/test_shared_db.py` | Unit tests | Migration, FTS5 | ~100 |
| `tests/memory/test_concurrency.py` | Stress test | 10 parallel writers | ~80 |
| `tests/memory/test_integration.py` | Integration | End-to-end with QueryLoop | ~100 |

### 2.2 Files to Modify

| File | Changes | Risk Level |
|------|---------|------------|
| `vibe/core/config.py` | Add WikiConfig, PageIndexConfig, RLMConfig, TripartiteMemoryConfig | Low |
| `vibe/harness/planner.py` | Add `wiki_hint` to PlanRequest; keyword-only `pageindex` param | Medium |
| `vibe/core/query_loop.py` | Add optional `wiki`/`pageindex` params; async retrieval before planner; Closable protocol in close() | Medium |
| `vibe/core/query_loop_factory.py` | Wire trace_store first, then wiki/pageindex when tripartite_enabled | Medium |
| `vibe/cli/main.py` | Add `memory wiki` subcommands (list, search, show, create, edit, index rebuild, expire) | Low |
| `vibe/core/context_compactor.py` | Add telemetry logging hooks | Low |
| `vibe/harness/memory/__init__.py` | Re-export legacy wiki for backward compat | Low |

---

## 3. Phase-by-Phase Execution Plan

### Phase 0: Foundation (Config + Models)
**Goal:** Establish type-safe contracts before implementation.

**Tasks:**
1. Add config models to `vibe/core/config.py`
2. Create `vibe/memory/models.py` with WikiPage, IndexNode dataclasses
3. Create `vibe/memory/__init__.py` package structure

**Acceptance:**
- `pytest tests/core/test_config.py` passes
- `from vibe.memory.models import WikiPage, IndexNode` works
- All new Pydantic models validate correctly

---

### Phase 1: Storage Layer (LLMWiki)
**Goal:** Implement the core wiki storage with all v4 requirements.

**Tasks:**
1. `LLMWiki` class with full CRUD
2. YAML frontmatter read/write using `yaml` stdlib
3. UUID generation via `uuid.uuid4()`
4. `[[slug]]` wiki link syntax with reverse index
5. `AsyncFileLock` (filelock>=3.8) with strict lock ordering
6. Quality gates: draft/verified status, TTL expiration
7. BM25 search via SQLite FTS5 (in shared_db)

**Key Design Decisions:**
- Slug generation: `title.lower().replace(' ', '-').replace('_', '-')`, strip non-alphanumeric
- Lock hierarchy: index lock ALWAYS before page locks, sorted by path
- Content hash for skip-reindex: `hashlib.sha256(content.encode()).hexdigest()[:16]`

**Acceptance:**
- `wiki.create_page()` → valid `.md` file with YAML frontmatter
- `wiki.update_page()` → updates `last_updated`, preserves unmodified fields
- `wiki.search_pages()` → BM25 ranked results
- `wiki.get_backlinks()` → resolves `[[slug]]` without O(N²) scan
- `wiki.expire_drafts()` → deletes drafts older than `ttl_days`
- Concurrency stress: 10 parallel writers, 0 corruption
- Unit test coverage ≥ 70%

---

### Phase 2: Index Layer (PageIndex)
**Goal:** Implement the reasoning-based routing layer.

**Tasks:**
1. `PageIndex` class with JSON tree load/save
2. `route()` method — async, returns ranked nodes with confidence
3. Deterministic tag-based partitioning (lexicographic sort of first tag)
4. Sub-index support with `sub_index_path`
5. Incremental rebuild (default) + full rebuild (manual)
6. Token counting for threshold detection

**Key Design Decisions:**
- Partitioning triggers: `token_threshold=4000` OR `max_nodes_per_index=100`
- Routing uses LLM client (cheap model) for reasoning over index tree
- Timeout guard: `asyncio.wait_for(route(), timeout=2.0)`
- JSON schema validation via Pydantic

**Acceptance:**
- `index.json` validates against Pydantic schema
- `pageindex.route(query)` returns ranked list with confidence scores
- Partitioning triggers correctly at thresholds
- Incremental rebuild updates only changed category
- Golden wiki test: 20 pages, 10 queries, measurable accuracy

---

### Phase 3: Planner Integration
**Goal:** Add wiki hint injection without changing planner tier logic.

**Tasks:**
1. Add `wiki_hint: str = ""` to `PlanRequest` dataclass
2. Add keyword-only `*, pageindex=None` to `HybridPlanner.__init__`
3. In `_keyword_plan()`, append `request.wiki_hint` to memory hints
4. In `QueryLoop.run()`, add async wiki retrieval BEFORE planner call

**Critical v4 Constraint:**
- PageIndex retrieval happens in `QueryLoop.run()` (async), NOT inside planner (sync)
- `asyncio.wait_for()` with 2s timeout; skip on timeout without error
- Planner remains fully synchronous

**Acceptance:**
- `tripartite_enabled=false` → eval suite identical (not byte-for-byte)
- `tripartite_enabled=true` → wiki hints appear in planner results
- All existing planner tests pass
- Eval suite pass rate does not regress by >2%

---

### Phase 4: QueryLoop Integration
**Goal:** Wire wiki lifecycle into the main loop.

**Tasks:**
1. Add optional `wiki` and `pageindex` params to `QueryLoop.__init__`
2. Add `_wiki_extract_task: asyncio.Task | None` for Phase 1b
3. Update `close()` to use Closable protocol:
   ```python
   for subsystem in [self.trace_store, self.feedback_engine, self.compactor, self.wiki]:
       if subsystem and hasattr(subsystem, 'close'):
           await subsystem.close()
   ```
4. Cancel pending extract task in `close()`

**Acceptance:**
- `QueryLoop` accepts `wiki` and `pageindex` params
- `close()` cancels pending extract task cleanly
- `close()` closes all subsystems via protocol
- All existing query loop tests pass

---

### Phase 5: Factory Wiring
**Goal:** Correct initialization order (trace_store before tripartite).

**Tasks:**
1. In `QueryLoopFactory.create()`, wire `trace_store` first
2. Conditionally create `LLMWiki` and `PageIndex` when `tripartite_enabled`
3. Pass them to `QueryLoop` constructor

**Acceptance:**
- Factory wiring test: trace_store is wired before wiki/pageindex
- `tripartite_enabled=false` → no wiki/pageindex created
- `tripartite_enabled=true` → wiki and pageindex properly initialized

---

### Phase 6: Shared Memory Database
**Goal:** Consolidate databases with schema versioning.

**Tasks:**
1. Create `SharedMemoryDB` class in `vibe/memory/shared_db.py`
2. Tables: `sessions`, `evals`, `wiki_chunks` (FTS5), `chunk_meta`, `_schema_version`
3. `MigrationManager` with explicit runner (not silent auto-migration)
4. Content hash check to skip re-indexing
5. Chunk sync: delete old + insert new (atomic transaction)

**Acceptance:**
- `memory.db` created with all tables
- `_schema_version` table tracks migration state
- Migration from `traces.db`/`evals.db` preserves data integrity
- FTS5 `wiki_chunks` uses `porter` tokenizer
- Content hash check skips unchanged re-indexing

---

### Phase 7: CLI Commands
**Goal:** Add user-facing wiki management commands.

**Tasks:**
1. `vibe memory wiki list [--tag] [--status]`
2. `vibe memory wiki search <query>`
3. `vibe memory wiki show <page_id>`
4. `vibe memory wiki create --title "..." --tags a,b,c` (opens $EDITOR)
5. `vibe memory wiki edit <page_id>` (opens $EDITOR)
6. `vibe memory wiki index rebuild`
7. `vibe memory wiki expire`

**Acceptance:**
- All commands execute without error
- `create`/`edit` open $EDITOR when available
- `search` returns BM25-ranked results
- `list` filters by tag and status correctly

---

### Phase 8: FlashLLMClient & Telemetry
**Goal:** Infrastructure for quality gates and Phase 2 trigger.

**Tasks:**
1. `FlashLLMClient` contract in `vibe/memory/flash_client.py`
2. Supports cheap model (local Ollama or API flash tier)
3. Fallback chain: skip contradiction detection if unavailable
4. `TelemetryCollector` in `vibe/memory/telemetry.py`
5. `ContextCompactor` logs: content size, strategy, token count
6. `QueryLoop` logs: session duration, total characters
7. Store telemetry in `memory.db` `_telemetry` table

**Acceptance:**
- FlashLLMClient routes to cheap model
- Fallback behavior when cheap model unavailable
- Telemetry table populated with compaction/query metrics
- Dashboard query: "% sessions with content >100K chars compactor couldn't handle"

---

### Phase 9: Unit Tests & Concurrency Stress
**Goal:** Verify correctness and robustness.

**Test Matrix:**

| Test | File | Coverage Target |
|------|------|-----------------|
| Wiki CRUD | `tests/memory/test_wiki.py` | 70%+ |
| Wiki concurrency | `tests/memory/test_concurrency.py` | 10 writers, 0 corruption |
| PageIndex routing | `tests/memory/test_pageindex.py` | Golden set accuracy |
| Shared DB migration | `tests/memory/test_shared_db.py` | Data integrity |
| Planner regression | `tests/test_planner.py` | No regression |
| QueryLoop integration | `tests/memory/test_integration.py` | End-to-end |
| Config validation | `tests/core/test_config.py` | All new models |

---

## 4. Subagent Execution Strategy

We use **parallel subagents** for maximum throughput:

```
Main Agent (coordination)
├── Subagent A: Phase 0 (Config + Models) — kimi-cli
├── Subagent B: Phase 1 (LLMWiki) — kimi-cli
├── Subagent C: Phase 2 (PageIndex) — kimi-cli
├── Subagent D: Phase 6 (Shared DB) — kimi-cli
└── After A-D complete:
    ├── Subagent E: Phase 3+4+5 (Planner + QueryLoop + Factory) — kimi-cli
    ├── Subagent F: Phase 7 (CLI) — kimi-cli
    └── Subagent G: Phase 8+9 (FlashClient + Telemetry + Tests) — kimi-cli
```

**Review gates:**
- After each subagent completes → Gemini CLI review
- After review approval → next phase
- User approval required between major phases

---

## 5. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| AsyncFileLock not available (filelock<3.8) | Graceful fallback to sync FileLock with warning |
| YAML import missing | Use `pyyaml` as optional dep; fallback to manual frontmatter parsing |
| FTS5 not available in SQLite | Graceful fallback to regular table + LIKE search |
| Planner regression | Comprehensive eval suite run before/after; -2% tolerance |
| Concurrency corruption | Stress test with 10 parallel writers; strict lock ordering |
| Migration data loss | Explicit MigrationManager; backup before migration; test on copy |

---

## 6. Definition of Done for Phase 1a

- [ ] All 10 new files created with proper docstrings
- [ ] All 7 modified files updated with backward compatibility
- [ ] Unit test coverage ≥ 70% for new modules
- [ ] Concurrency stress test passes (0 corruption)
- [ ] Planner eval suite shows <2% regression
- [ ] All existing tests pass
- [ ] CLI commands functional
- [ ] Config validation works with env overrides
- [ ] Migration from old `traces.db`/`evals.db` preserves data
- [ ] Code review approved (Gemini CLI)

---

*Plan written. Awaiting user approval to begin Phase 0 implementation.*
