# Tripartite Memory System v3 — Consolidated Design Review

**Reviewers:** Gemini CLI (deep methodology prompt) + Kimi CLI (kimi-for-coding) + Hermes Agent synthesis
**Date:** 2026-04-26
**Design Doc:** `/Users/rsong/DevSpace/vibe-agent/docs/TRIPARTITE_MEMORY_DESIGN.md` (v3)
**Codebase:** `/Users/rsong/DevSpace/vibe-agent/`

---

## Executive Summary

The v3 Tripartite Memory System design is a **risky partial improvement** over v1/v2. It correctly defers the dangerous RLM layer and makes wiki writes opt-in, but introduces **new critical flaws** in its core retrieval mechanism (PageIndex), contains **unresolved API mismatches** with the synchronous planner, and understates the **operational burden** of quality gates and index maintenance.

**Verdict: CONDITIONAL APPROVAL** — Architecture is sound at a high level but requires substantial revision to the routing layer, planner integration contract, and migration strategy before implementation.

---

## 1. Critical Blockers (Must Fix Before Implementation)

### P0-1: Sync/Async Planner Mismatch — Blocks Implementation Entirely

**Finding (Kimi):** `HybridPlanner.plan()` is **synchronous** (`def plan(self, request: PlanRequest) -> PlanResult`), but `PageIndex.route()` requires an **async LLM call**. The design injects `pageindex.route(request.query)` inside `_keyword_plan()` without resolving this boundary.

**Impact:** Cannot call async LLM from sync planner without either (a) blocking the event loop with `asyncio.run()` (dangerous), or (b) refactoring `HybridPlanner.plan()` to `async` — a breaking change rippling through `QueryLoop`, tests, and all callers.

**Fix (Kimi):** Move wiki retrieval **before** `planner.plan()`, in `QueryLoop.run()` where already async. Pass retrieved wiki context as part of `PlanRequest.history_summary` or a new field. Preserves planner sync semantics and separates retrieval from planning.

### P0-2: PageIndex Adds 1–3s Blocking Latency Per Query

**Finding (Gemini + Kimi):** The design documents 1–3s latency for `PageIndex.route()` and frames it as "augmentation, not a tier." But in existing code, memory augmentation happens **inside** `_keyword_plan()` before any result is returned. Inserting `pageindex.route()` there blocks the entire planner.

**Current vs Proposed:**
| Metric | Current (trace_store) | Proposed (PageIndex) |
|--------|----------------------|----------------------|
| Latency | <100ms local | 1–3s API round-trip |
| Cost | 0 tokens | ~500–2000 tokens/query |
| Determinism | High | Low (sampling temp > 0) |
| Offline capable | Yes | No |

**Fix (Gemini):** Wrap `pageindex.route()` in `concurrent.futures.ThreadPoolExecutor` with strict 2.0s timeout. Fail gracefully, preserve 5ms baseline.

**Fix (Kimi):** Make PageIndex a **fallback** that only activates when local retrieval returns no results, not an always-on augmentation.

### P0-3: Factory Never Wires Trace Store — Memory System Is Dead Code

**Finding (Kimi):** `QueryLoopFactory.create()` (line 112) does **not** instantiate or pass a `trace_store` to `QueryLoop`. `trace_store` is always `None` in factory-created loops, making the existing memory augmentation dead code for CLI users.

**Fix (Kimi):** Fix factory to read `TraceStoreConfig` and instantiate `trace_store` before adding tripartite components. If intentionally omitted, document why.

---

## 2. Major Issues (Fix Before Phase 1a Ships)

### P1-1: Vector Search Keyword Pre-Filter Defeats Semantic Search

**Finding (Gemini):** `SQLiteTraceStore.get_similar_sessions_vector()` uses aggressive keyword pre-filtering (`LOWER(content) LIKE ?`) that drops vector matches if they don't share exact keywords with the query. This prevents true semantic matching (e.g., "slow database" vs "high query latency").

**Fix (Gemini):** Remove the aggressive keyword pre-filter. Perform full vector scan for true semantic matching:
```python
# Remove pre-filter block (lines 233-245 in trace_store.py)
# Query all embeddings directly:
rows = conn.execute("""
    SELECT se.session_id, se.embedding, s.start_time, s.success, s.model
    FROM session_embeddings se
    JOIN sessions s ON se.session_id = s.id
""").fetchall()
```

### P1-2: Contradiction Detection Requires "Cheap LLM" Infrastructure That Doesn't Exist

**Finding (Kimi):** Quality gates (§3.5) require contradiction detection via "cheap LLM call." The project has no "cheap model" routing infrastructure. `LLMClient` in `model_gateway.py` has fallback chains but no explicit cost/tier routing.

**Impact:** Quality gates will either silently fail or cost too much.

**Fix (Kimi):** Define a `FlashLLMClient` wrapper or model profile before implementing gates. Without it, contradiction detection is unimplementable as specified.

### P1-3: File Locking in Async Code Is a Footgun

**Finding (Kimi + Gemini):** The design proposes `FileLock` for wiki concurrency, but `filelock` is not a current dependency. More critically, `FileLock` is **thread-blocking**, not async-friendly. If an asyncio event loop thread acquires a file lock and another coroutine needs it, the entire loop blocks.

**Fix (Kimi):** Add `filelock>=3.8` to dependencies and use `AsyncFileLock` exclusively. Write a dedicated concurrency test simulating two asyncio event loops in different processes contending for the same wiki page.

### P1-4: Database Migration Has No Versioning Strategy

**Finding (Kimi):** The design says existing DBs are "migrated on first boot" to `memory.db`. There is no schema version table, migration rollback strategy, or handling for concurrent processes.

**Fix (Kimi):** Implement a `MigrationManager` with Alembic-style versioning, or at minimum a `_schema_version` table in `memory.db`. Do not perform silent auto-migration on first access.

---

## 3. Design Flaws (Fix Before Finalizing Design)

### P2-1: BM25 Score Threshold Is Mathematically Bogus

**Finding (Kimi):** The design specifies "BM25 similarity < 0.9" as a novelty gate. BM25 scores are **unbounded and not normalized** to [0, 1]. A score of 0.9 is meaningless without reference to corpus statistics.

**Fix (Kimi):** Use a percentile-based threshold (e.g., top-k retrieval), or switch to cosine similarity on embeddings if a normalized score is required.

### P2-2: UUID-Based Wiki Links Are Human-Hostile

**Finding (Kimi):** The v3 schema mandates `[[UUID]]` for wiki links. A user editing `~/.vibe/wiki/*.md` sees `[[a1b2c3d4-e5f6-7890-abcd-ef1234567890]]` instead of `[[Database Scaling]]`.

**Fix (Kimi):** Store `[[slug]]` or `[[Title]]` in markdown content. Resolve links via the index mapping at read time, not write time.

### P2-3: Internal Contradictions Between Design Documents

**Finding (Kimi):** The v3 doc and earlier `TRIPARTITE_DESIGN.md` describe incompatible architectures:

| Layer | v1/v2 (TRIPARTITE_DESIGN.md) | v3 (TRIPARTITE_MEMORY_DESIGN.md) |
|-------|------------------------------|----------------------------------|
| Execution | `RLMExecutor` with RestrictedPython + OS sandbox + Python REPL | `RLMInterpreter` with declarative JSON tool-calling loop, no REPL |
| Curation | Background thread/worker with `CurationQueue` | `asyncio.create_task()` for extraction |
| Integration | `TripartiteMemoryManager` orchestrator | Direct wiring into `QueryLoop` and `HybridPlanner` |

**Fix (Kimi):** Archive `TRIPARTITE_DESIGN.md` with a deprecation header, or merge both into a single canonical doc. Do not leave contradictory designs in `docs/`.

### P2-4: "Byte-for-Byte Identical" Is a False Promise

**Finding (Kimi):** The design claims that when `tripartite_enabled=false`, planner behavior is "byte-for-byte identical." But adding optional parameters to `HybridPlanner.__init__` changes the method signature. Python code using positional arguments (`HybridPlanner(trace_store, path, client)`) will break.

**Fix (Kimi):** Use keyword-only arguments for new parameters, or add them to an optional `config` dict rather than the constructor signature.

### P2-5: Phase 2 Trigger Condition Is Unmeasurable

**Finding (Kimi):** The RLM deferral trigger (§5.1) is: "Enable RLM when ≥5% of sessions in a 30-day window encounter content >100K chars that the compactor cannot handle." The codebase has **no telemetry** for content sizes, compactor strategy outcomes, or session-level character counts.

**Fix (Kimi):** Add metrics collection to `ContextCompactor` and `QueryLoop` before shipping Phase 1. Otherwise Phase 2 will never have data to justify its existence.

---

## 4. Migration Assessment

### Phase 1a Is Not "Shippable" — It's a Large Cross-Cutting Refactor

**Finding (Kimi):** The design labels Phase 1a as "Standalone Wiki + PageIndex (Shippable)" and lists 4 new files + 5 modified files. In reality:

| Work Item | Complexity | Risk |
|-----------|-----------|------|
| New `vibe/memory/` package with LLMWiki | Medium | File locking, YAML parsing, UUID gen |
| PageIndex with JSON tree + partitioning | High | LLM calls in sync planner, non-determinism |
| Shared `memory.db` with FTS5 | Medium | Migration from existing DBs, schema versioning |
| Planner integration | High | Sync/async boundary, timeout handling |
| QueryLoop async task lifecycle | Medium | Task cancellation, resource cleanup |
| CLI subcommands | Low | Typer boilerplate |
| Config schema changes | Low | Pydantic models |
| Backward compatibility tests | High | "Byte-for-byte identical" is hard to verify |
| Golden wiki test (20 pages, 10 queries) | High | Requires human annotation maintenance |
| Concurrency stress test | High | Async file locking is subtle |

**Verdict:** 2–3 weeks for one engineer, not a "Phase 1a" sprint.

### Old Wiki Migration Is Hand-Waved

**Finding (Kimi):** Existing `wiki.py` stores flat markdown with no YAML frontmatter, no UUIDs, no citations. The design says "import pages into new schema and generate index.json" but doesn't specify how to generate UUIDs, assign dates, or handle title collisions.

**Fix (Kimi):** Treat old wiki pages as **read-only legacy import**. Assign deterministic UUIDs (UUID5 from title), set `status: legacy`, `date_created: filesystem mtime`, and require user confirmation before promotion to `draft`.

---

## 5. What the Reviewers Got Right (Validation)

Both Gemini and Kimi independently identified these issues, confirming they are real:

1. **Sync/async planner mismatch** — Both found it, Kimi provided the exact fix
2. **PageIndex latency regression** — Both quantified it, proposed different but valid fixes
3. **Keyword pre-filter defeating semantic search** — Gemini found with exact line number
4. **File locking in async code** — Both identified, Kimi specified `AsyncFileLock` version
5. **Stateful coordinators** — Gemini found `FeedbackCoordinator._retry_count`
6. **Database migration underspecified** — Kimi provided concrete `MigrationManager` recommendation

---

## 6. Unique Findings Per Reviewer

### Gemini-Only Findings
- `QueryLoop.run()` is 110+ lines, violating "thin orchestrator (< 40 lines)" claim
- `FeedbackCoordinator` stores `_retry_count`, violating statelessness
- `QueryLoop.close()` lifecycle is incomplete (doesn't close `trace_store`, `feedback_engine`, `context_compactor`)
- Config schema extension risk: `VibeConfig` uses `extra="ignore"`, so misspelled keys silently fail

### Kimi-Only Findings
- Factory never wires `trace_store` — existing memory system is dead code for CLI users
- BM25 threshold is mathematically bogus (scores unbounded, not normalized)
- UUID-based wiki links are human-hostile
- Internal contradictions between v1/v2 and v3 design documents
- "Byte-for-byte identical" is false promise due to constructor signature changes
- Quality gates require "cheap LLM" infrastructure that doesn't exist
- TTL expiration is manual CLI-only (no background scheduler)
- Phase 2 trigger condition is unmeasurable (no telemetry)
- Hierarchical partitioning is non-deterministic (LLM categorization varies between runs)
- Phase 1a is a 2-3 week cross-cutting refactor, not a standalone feature
- Old wiki migration is hand-waved with no schema-compatible metadata path

---

## 7. Recommendations for Design Revision

### Immediate (Before Any Code Is Written)
1. **Resolve sync/async boundary** — Move PageIndex retrieval out of planner, into `QueryLoop.run()`
2. **Fix factory to wire trace_store** — Otherwise new memory system is dead code
3. **Archive or merge contradictory design docs** — v1/v2 and v3 describe incompatible architectures
4. **Replace BM25 threshold** — Use percentile-based or cosine similarity
5. **Use human-readable wiki links** — `[[slug]]` not `[[UUID]]`

### Before Phase 1a Implementation
6. **Define `FlashLLMClient` contract** — Required for quality gates
7. **Add `filelock>=3.8` dependency** — Use `AsyncFileLock` exclusively
8. **Implement schema version table** — For database migration safety
9. **Add telemetry to ContextCompactor** — Required for Phase 2 trigger
10. **Lower test coverage target** — 70% for Phase 1a, invest in integration tests

### Before Phase 2 Planning
11. **Remove RLM-specific config from Phase 1a** — Include only `enabled: bool = False` placeholder
12. **Define measurable trigger conditions** — Based on actual telemetry, not hand-waved percentages

---

## Appendix: Review Methodology

This review used a **deep critique methodology** developed from analyzing prior design reviews:

1. **Direct codebase inspection** — Read actual source files, not just design docs
2. **Trace specific code paths** — Follow complete request lifecycle through current and proposed systems
3. **Identify contradictions** — Compare design claims against implementation reality with file paths and line numbers
4. **Evaluate economic/operational costs** — Quantify latency, token cost, memory overhead, new failure modes
5. **Propose concrete fixes** — Real code snippets, not pseudocode, referencing existing patterns
6. **Assess migration strategy** — Examine constructor signatures and factory wiring for backward compatibility

Both reviewers were given the same codebase access and design document. Kimi produced 320 lines of critique; Gemini produced 140 lines. The consolidated document above synthesizes both, deduplicates overlapping findings, and preserves unique insights from each reviewer.

---

*Review conducted 2026-04-26 against design v3 and codebase at `/Users/rsong/DevSpace/vibe-agent/`*
