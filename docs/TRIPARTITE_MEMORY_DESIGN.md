# Vibe Agent — Tripartite Memory System: Design Document v3

**Date:** 2026-04-26  
**Scope:** Merge the Tripartite Memory System into the existing `vibe-agent` memory architecture  
**Status:** Design Phase v3 — Addresses Gemini v1/v2 + Claude v1/v2 critiques  
**Target File:** `~/DevSpace/vibe-agent/docs/TRIPARTITE_MEMORY_DESIGN.md`

---

## 1. Executive Summary

The current `vibe-agent` memory system is a multi-tier persistence layer with:
- **Trace store** (SQLite/JSON) for episodic session logging
- **Eval store** (SQLite) for benchmark regression tracking
- **Context compactor** (in-flight token-budget compaction)
- **Planner query cache** (in-memory LRU)
- **Wiki memory** (archived flat markdown files)

The **Tripartite Memory System** replaces the vector-based similarity search paradigm with a human-textbook model:
1. **The Index** (PageIndex) — a JSON "Table of Contents" that the LLM reasons over to route queries
2. **The Storage** (LLM Wiki) — interlinked Markdown files with YAML frontmatter, incrementally maintained
3. **The Execution** (RLM) — a declarative JSON tool-calling loop for processing documents beyond context limits

**Key principle for v3:** Phase 1 (Wiki + PageIndex) is **explicit, opt-in memory augmentation** — not a planner tier. The user triggers wiki writes. Phase 2 (RLM) is deferred until real usage data justifies it. Auto-extraction is gated behind quality signals.

---

## 2. Current State vs. Target State

### 2.1 Current Memory Architecture

| Component | Purpose | Persistence | Key Gap |
|-----------|---------|-------------|---------|
| `SQLiteTraceStore` | Session logging + vector similarity | `~/.vibe/memory/traces.db` | Brute-force vector search (O(N) numpy dot) |
| `JSONTraceStore` | File-based session logging | `~/.vibe/memory/traces.json` | Full rewrite per log |
| `EvalStore` | Benchmark results | `~/.vibe/memory/evals.db` | Well-scoped |
| `ContextCompactor` | Token-budget compaction | In-flight only | TRUNCATE/LLM_SUMMARIZE/OFFLOAD/DROP |
| `HybridPlanner` | Tool/skill selection + query cache | In-memory LRU | 4-tier planner (keyword → embedding → LLM → fallback) |
| `QueryLoop.messages` | Conversation history | None (in-memory) | Lost on process exit |
| `WikiMemory` (archived) | Cross-session knowledge pages | `~/.vibe/wiki/*.md` | **Inactive** |

### 2.2 Target Architecture (Tripartite Integration)

| Layer | Replaces / Augments | New Component | Persistence |
|-------|---------------------|---------------|-------------|
| **Index** | Augments planner with memory hints | `PageIndex` | `~/.vibe/memory/index.json` |
| **Storage** | Revives `WikiMemory` as opt-in knowledge store | `LLMWiki` | `~/.vibe/wiki/*.md` |
| **Execution** | Deferred to Phase 2 | `RLMEngine` | In-flight declarative loop |
| **Trace Store** | Retained unchanged (vector search kept as optional) | `SQLiteTraceStore` | `~/.vibe/memory/traces.db` |
| **Eval Store** | Unchanged | `EvalStore` | `~/.vibe/memory/evals.db` |
| **Planner** | Retains all 4 tiers unchanged; adds wiki hint injection | `HybridPlanner` | In-memory LRU |

---

## 3. Layer 1: The Storage Layer (LLM Wiki)

### 3.1 Concept

Andrej Karpathy's "LLM Wiki" pattern: the LLM incrementally builds and maintains a persistent, interlinked collection of Markdown files. Knowledge is compiled once and kept current.

**v3 principle:** Wiki writes are **explicit and gated**, not automatic. The user triggers creation with `vibe memory wiki create` or a confirmation prompt. Auto-extraction (Phase 1b) requires a quality signal and is disabled by default.

### 3.2 File Schema

All files saved as `.md` with YAML frontmatter:

```yaml
---
id: a1b2c3d4-e5f6-7890-abcd-ef1234567890  # UUID, never changes
title: Infrastructure Logs
date_created: 2026-04-10
last_updated: 2026-04-26
tags: [database, scaling, servers]
status: draft|verified  # See §3.5 for promotion rules
citations:
  - session: session_uuid_abc123
    date: 2026-04-10
    summary: "Database read-replica lag identified as scaling bottleneck"
ttl_days: 30  # Auto-expire draft pages after N days
---

# Infrastructure Logs

Content goes here with [[a1b2c3d4]] links to other docs...
```

**Schema decisions (v3):**
- `id`: UUID (not `doc_004` sequence) — eliminates race conditions
- `citations`: Inline provenance, not just `source_session` — survives trace store retention
- `ttl_days`: Auto-expiration for draft pages — prevents garbage accumulation
- `status`: `draft` (default) or `verified` — see §3.5 for promotion rules
- Wiki links use `[[UUID]]` with title as rendered label — renames don't break links

### 3.3 Wiki Operations API

```python
class LLMWiki:
    def create_page(self, title: str, content: str, tags: list[str],
                    citations: list[dict], status: str = "draft") -> WikiPage
    def update_page(self, page_id: str, content: str | None = None,
                    tags: list[str] | None = None, citations: list[dict] | None = None) -> WikiPage
    def get_page(self, page_id: str) -> WikiPage | None
    def search_pages(self, query: str, limit: int = 10) -> list[WikiPage]
    def list_pages(self, tag: str | None = None, status: str | None = None) -> list[WikiPage]
    def delete_page(self, page_id: str) -> bool
    def get_backlinks(self, page_id: str) -> list[WikiPage]
    def expire_drafts(self, cutoff_days: int = 30) -> int  # Returns count expired
```

### 3.4 Concurrency Safety (File Locking)

All write operations use `filelock` with strict lock ordering to prevent deadlocks:

```python
from filelock import FileLock

# Lock hierarchy rule: index lock ALWAYS acquired first, then page locks
# This prevents the rebuild() vs update_page() deadlock

with FileLock(f"{index_path}.lock"):  # 1. Index lock (outer)
    for page in pages:
        with FileLock(f"{page.path}.lock"):  # 2. Page lock (inner)
            read_modify_write(page)
```

**Rules:**
- Single-page edits: acquire page lock only
- Rebuild operations: acquire index lock first, then page locks in deterministic order (sorted by path)
- No nested page lock acquisitions in reverse order

### 3.5 Quality Gates and Verification Lifecycle

**Status promotion rules:**

| Status | How it enters | How it promotes | How it exits |
|--------|---------------|-----------------|--------------|
| `draft` | Default on creation | To `verified`: requires ≥2 citations from distinct sessions AND no contradictions detected in wiki | To `expired`: after `ttl_days` without update |
| `verified` | Promotion from draft | N/A — stays verified unless manually demoted | To `draft`: if contradicted by new evidence |
| `expired` | Auto-expiration of draft | N/A — candidate for deletion | Deleted by `expire_drafts()` or manual cleanup |

**Contradiction detection:** Before writing/updating a page, query the wiki for pages with overlapping tags. Use a cheap LLM call (flash model) to check for factual conflicts. If contradiction detected, flag both pages for review and keep the new page as `draft`.

**Novelty signal for auto-extraction (Phase 1b):**
- Only extract if session contains ≥1 novel tool result (new file path, new command, new error)
- Only extract if the extractor LLM assigns confidence ≥0.8
- Only extract if the content is not a near-duplicate of an existing page (BM25 similarity < 0.9)

### 3.6 Integration with QueryLoop

**Phase 1a (default, manual):**
- User runs `vibe memory wiki create` or `vibe memory save` to explicitly save session insights
- No automatic extraction at session end

**Phase 1b (optional, gated auto-extraction):**
- Config: `memory.wiki.auto_extract: false` (default)
- When enabled, extraction runs via `asyncio.create_task()` (not `threading.Thread`)
- Task reference stored on `QueryLoop`; cancelled in `close()` if still running
- Extraction prompt template is configurable; defaults to extracting decisions, file edits, and errors only

```python
class QueryLoop:
    def __init__(self, ..., wiki: LLMWiki | None = None):
        self.wiki = wiki
        self._wiki_extract_task: asyncio.Task | None = None
    
    async def close(self) -> None:
        if self._wiki_extract_task and not self._wiki_extract_task.done():
            self._wiki_extract_task.cancel()
            try:
                await self._wiki_extract_task
            except asyncio.CancelledError:
                pass
        # ... existing close logic
```

### 3.7 What Replaces What

| Current | Replacement | Rationale |
|---------|-------------|-----------|
| `trace_store.get_similar_sessions()` (vector search) | **Kept unchanged** | Trace store memory augmentation continues working; wiki is additive |
| `WikiMemory` (archived) | `LLMWiki` (active, enhanced) | Revive with proper schema, quality gates, and QueryLoop wiring |
| Brute-force numpy dot product | **Kept as optional fallback** | fastText remains available; tripartite is additive, not replacement |

---

## 4. Layer 2: The Index Layer (PageIndex)

### 4.1 Concept

PageIndex: a vectorless, reasoning-based RAG system. The LLM reads a JSON "Table of Contents" and uses logic to decide which sections hold the answer.

**v3 principle:** PageIndex is **memory augmentation**, not a planner tier. It runs alongside (not instead of) the existing planner tiers. It injects wiki-based hints into the system prompt, similar to how `trace_store.get_similar_sessions()` injects historical context today.

### 4.2 Index Schema

Single `index.json` file, hierarchical tree with sub-index support:

```json
{
  "wiki_index": {
    "node_id": "root_01",
    "title": "Master Knowledge Base",
    "description": "Top-level index for all agent knowledge.",
    "sub_nodes": [
      {
        "node_id": "cat_dev",
        "title": "Development",
        "description": "Coding, tools, and development workflows.",
        "sub_index_path": "index_dev.json",
        "tags": ["dev", "coding"],
        "sub_nodes": []
      },
      {
        "node_id": "doc_004",
        "title": "Infrastructure Logs",
        "description": "Historical data on server performance, database scaling, and outages.",
        "file_path": "/wiki/infrastructure_logs.md",
        "tags": ["database", "scaling", "servers"],
        "sub_nodes": []
      }
    ]
  }
}
```

**New field:** `sub_index_path` — references a category sub-index file. Enables hierarchical partitioning.

### 4.3 Hierarchical Index Partitioning

**Trigger conditions:** Partitioning activates when EITHER:
- Root index exceeds `token_threshold` (default: 4000 tokens), OR
- Root index exceeds `max_nodes_per_index` (default: 100 nodes)

**Whichever threshold is hit first triggers partitioning.**

**Partitioning algorithm:**
1. LLM categorizes all pages into buckets based on tags (or uses existing tag taxonomy)
2. Each bucket becomes a sub-index file (`index_{category}.json`)
3. Root index is rewritten with category summary nodes (not individual pages)
4. Both root and sub-indexes are locked during rebuild

**Routing with sub-indexes:**
```
1. Load root index.json into LLM context
2. LLM reasons over category summaries → selects relevant sub-index
3. Load sub-index → LLM reasons over page nodes
4. Return ranked list of node_ids with confidence scores
5. Caller fetches corresponding wiki pages from LLMWiki
```

**Latency target:** 1–3s for full routing (root + sub-index). This is realistic for LLM-based reasoning and is documented as such.

### 4.4 Index Operations API

```python
class PageIndex:
    def load(self) -> IndexTree
    def route(self, query: str) -> list[IndexNode]  # Returns ranked list
    def add_node(self, parent_id: str, title: str, description: str,
                 file_path: str, tags: list[str]) -> IndexNode
    def update_node(self, node_id: str, **fields) -> IndexNode
    def remove_node(self, node_id: str) -> bool
    def rebuild(self, wiki: LLMWiki, incremental: bool = True) -> None
    def _partition_if_needed(self) -> None
```

**Incremental rebuild (default):** Only re-index the changed page and its parent category. Full rebuild is manual (`vibe memory wiki index rebuild`).

### 4.5 Integration with HybridPlanner (Memory Augmentation, Not Tier)

PageIndex does NOT replace any planner tier. Instead, it augments the existing trace-store memory injection:

```python
# In HybridPlanner._keyword_plan() (existing code, line 259-264):
memory_hint = ""
if self.trace_store is not None:
    similar = self.trace_store.get_similar_sessions(request.query, limit=3)
    if similar:
        memory_hint = "\n\n## Historical Context\n..."

# NEW (v3): Add wiki-based augmentation alongside trace store
if self.pageindex is not None:
    wiki_nodes = self.pageindex.route(request.query)
    if wiki_nodes:
        wiki_hint = "\n\n## Relevant Knowledge\n" + "\n".join(
            f"- [[{n.node_id}]] {n.title}: {n.description}" for n in wiki_nodes[:3]
        )
        memory_hint += wiki_hint
```

**Why this works:**
- Planner latency is unchanged (~5ms keyword / ~5ms embedding) because PageIndex runs **after** keyword/embedding tiers have already selected tools
- PageIndex only adds hints to `system_prompt_append` — it does not block tool selection
- If PageIndex is slow (1–3s), the planner can skip it with a timeout guard (default: 2s)

### 4.6 Hybrid Pre-Filter (BM25 + Optional Embeddings)

To avoid loading massive markdown files into the RLM when not needed, implement a lightweight SQLite pre-filter in the **shared** memory database:

**Shared database:** `~/.vibe/memory/memory.db` (replaces separate `traces.db`, `evals.db`, `wiki_chunks.db`)

```sql
-- Single database, multiple tables
CREATE TABLE sessions (...);        -- migrated from traces.db
CREATE TABLE evals (...);           -- migrated from evals.db
CREATE VIRTUAL TABLE wiki_chunks USING fts5(
    chunk_id, page_id, content, tokenize='porter'
);
CREATE TABLE chunk_meta (
    chunk_id TEXT PRIMARY KEY,
    page_id TEXT,
    start_offset INTEGER,
    end_offset INTEGER
);
```

**BM25 (FTS5):** Exact keyword matching for error codes, names, strict identifiers.  
**Optional semantic:** If `fasttext` is available, use `sqlite-vec` for conceptual proximity.  
**Fallback:** BM25-only is sufficient when embeddings are unavailable.

**Chunk sync strategy:** On wiki page edit, delete all chunks for that `page_id`, then re-chunk and re-insert. This is O(chunks) per edit, not O(total chunks).

---

## 5. Layer 3: The Execution Layer (RLM Engine) — PHASE 2, DEFERRED

### 5.1 Status

The RLM Engine is **deferred to Phase 2**. Phase 1 ships without it. The rationale:
- Modern context windows (200K–1M tokens) make "document larger than context" rare
- The existing `ContextCompactor` handles 8K-token budgets adequately
- The RLM adds significant complexity (declarative plans, sub-LLM orchestration, rate limiting) for an edge case

**Phase 2 trigger condition:** Enable RLM when ≥5% of sessions in a 30-day window encounter content >100K chars that the compactor cannot handle.

### 5.2 Design (Ready for Phase 2)

When Phase 2 activates, the RLMEngine uses a **declarative JSON tool-calling loop** (no Python REPL):

```python
class RLMInterpreter:
    ALLOWED_TOOLS = {
        "load_chunk": _load_chunk,
        "query_chunk": _query_chunk,
        "merge_answers": _merge_answers,
        "filter_chunks": _filter_chunks,
    }
    
    async def execute_plan(self, plan: RLMPlan) -> str:
        self._validate_plan(plan)  # Schema + whitelist + arg sanitization
        return await self._execute_steps(plan.steps)
```

**Plan validation (CRITICAL-1 fix):**
```python
def _validate_plan(self, plan: dict) -> None:
    # 1. JSONSchema validation
    jsonschema.validate(plan, RLM_PLAN_SCHEMA)
    
    # 2. Tool name whitelist
    for step in plan["steps"]:
        if step["tool"] not in self.ALLOWED_TOOLS:
            raise RLMValidationError(f"Unknown tool: {step['tool']}")
    
    # 3. Argument sanitization (SecretRedactor on query_chunk prompts)
    for step in plan["steps"]:
        if step["tool"] == "query_chunk":
            prompt = step["args"].get("query", "")
            if self.redactor.scan(prompt):
                raise RLMValidationError("Prompt contains sensitive patterns")
    
    # 4. No circular references in output_var dependencies
    self._check_acyclic(plan["steps"])
```

**Plan generation:** The main LLM generates the plan via structured output (JSON mode). The prompt explicitly constrains available tools and requires the plan to be acyclic.

**Sub-LLM call management:**
- Default `max_concurrency=4`
- VRAM-aware: detect via `nvidia-smi` (Linux), `system_profiler` (macOS), or API query
- Token-bucket rate limiting: `TokenBucket(rpm=60, tpm=100000)`
- Per-step timeout (not per-query):
  ```python
  STEP_TIMEOUTS = {
      "load_chunk": 1.0,
      "query_chunk": 30.0,  # Configurable by sub-LLM model
      "merge_answers": 10.0,
      "filter_chunks": 2.0,
  }
  ```
- Per-chunk retry: exponential backoff, max 3 retries
- Fallback: if >50% of chunks fail, truncate and summarize directly

---

## 6. Data Flow: End-to-End Query Lifecycle

### 6.1 Typical Session (Phase 1a — Manual Wiki)

```
1. User types query in CLI
   └── query_loop.run("What database scaling problems did we have last month?")

2. QueryLoop appends user message to self.messages

3. Planning phase (UNCHANGED from existing behavior)
   └── HybridPlanner.plan(PlanRequest(query=...))
       ├── Tier 1: Keyword match → miss
       ├── Tier 2: fastText embedding → miss (or hit, if installed)
       ├── Tier 3: LLM router → selects relevant tools
       └── Tier 4: Fallback (not needed)
       
       └── Memory augmentation (NEW):
           ├── trace_store.get_similar_sessions() → injects historical context
           └── pageindex.route() → injects wiki hints (if tripartite enabled)
               "## Relevant Knowledge\n- [[uuid]] Infrastructure Logs (database, scaling)"

4. Main loop iteration (UNCHANGED)
   ├── Build LLM messages
   ├── Check compaction
   ├── LLMClient.complete(messages, tools)
   └── Process response

5. Session ends
   ├── TraceStore.log_session() (episodic logging, unchanged)
   └── NO automatic wiki extraction (Phase 1a)
```

### 6.2 Explicit Wiki Save (User-Triggered)

```
User runs: vibe memory save

1. QueryLoop checks self.messages for novel content
2. Extractor LLM (cheap model) generates wiki page draft
3. User confirms or edits in $EDITOR
4. wiki.create_page(title="...", content="...", citations=[...])
5. pageindex.add_node(parent_id="root_01", ...)
```

### 6.3 Massive Document Query (Phase 2 — RLM, Deferred)

```
1. User asks: "Summarize all infrastructure decisions from the past year"

2. Planner routes to doc_004 (Infrastructure Logs)
   └── Wiki page is 500K characters

3. QueryLoop detects content > 100K chars
   └── Delegates to RLMEngine.query(...)

4. RLMEngine executes validated declarative plan:
   ├── Chunk into 10 chunks of ~50K (header-based)
   ├── Generate JSON plan (structured output from main LLM)
   ├── Validate plan (schema, whitelist, sanitization)
   ├── Execute with max_concurrency=4, rate limiting, per-step timeouts
   ├── Collect partial answers (retry on failure)
   ├── Merge answers
   └── Return final answer

5. QueryLoop receives final answer, appends to messages, yields to user
```

---

## 7. Component Changes & Migration Plan

### 7.1 Phase 1a: Standalone Wiki + PageIndex (Shippable)

**Files to create:**

| File | Purpose |
|------|---------|
| `vibe/memory/wiki.py` | `LLMWiki` class — CRUD, YAML frontmatter, file locking, quality gates |
| `vibe/memory/pageindex.py` | `PageIndex` class — JSON index, hierarchical partitioning |
| `vibe/memory/rate_limiter.py` | `TokenBucket` for future RLM use |
| `vibe/memory/__init__.py` | Unified exports |

**Files to modify:**

| File | Changes |
|------|---------|
| `vibe/harness/planner.py` | Add `pageindex` param; inject wiki hints in `_keyword_plan()` alongside trace store hints |
| `vibe/core/config.py` | Add `TripartiteMemoryConfig` Pydantic model |
| `vibe/core/query_loop.py` | Add optional `wiki` param; add `_wiki_extract_task` lifecycle |
| `vibe/core/query_loop_factory.py` | Wire `LLMWiki`, `PageIndex` when `tripartite_enabled=true` |
| `vibe/cli/main.py` | Add `memory wiki` subcommands |

**Files unchanged:**
- `vibe/harness/memory/trace_store.py` — vector search kept as-is
- `vibe/core/context_compactor.py` — no changes

### 7.2 Phase 1b: Gated Auto-Extraction (Opt-In)

**Adds to Phase 1a:**
- Config: `memory.wiki.auto_extract: false` (default)
- Extraction prompt template (configurable)
- Novelty signal detector (new tool results, new file paths)
- Confidence threshold gate (extractor LLM assigns 0–1 score)
- `asyncio.create_task()` for non-blocking extraction

### 7.3 Phase 2: RLM Engine (Deferred)

**Files to create:**
- `vibe/memory/rlm_engine.py` — `RLMEngine` + `RLMInterpreter`
- `vibe/memory/wiki_chunks.py` — FTS5 chunk store in shared `memory.db`

**Files to modify:**
- `vibe/core/query_loop.py` — Add RLM delegation for content >100K chars
- `vibe/core/query_loop_factory.py` — Wire `RLMEngine`

### 7.4 Backward Compatibility

- **Config flag:** `memory.tripartite_enabled: bool = False` (default). When false, zero behavior changes.
- **Trace store:** `session_embeddings` table kept unchanged. `get_similar_sessions()` continues working.
- **Planner:** All 4 tiers unchanged. Wiki hint injection is additive and times out after 2s if slow.
- **Migration:** On first boot with tripartite enabled, if `~/.vibe/wiki/` exists from old `WikiMemory`, import pages into new schema and generate `index.json`.

---

## 8. Implementation Goals

### Goal 1: LLM Wiki Storage Layer (Phase 1a)
**Objective:** Implement `LLMWiki` with full CRUD, YAML frontmatter, UUID IDs, file locking, quality gates.

**Acceptance Criteria:**
- [ ] `wiki.create_page()` creates `.md` with valid YAML frontmatter and UUID `id`
- [ ] `wiki.update_page()` updates `last_updated`, preserves unmodified fields, adds citations
- [ ] `wiki.search_pages()` returns results ranked by BM25 on title/tags/content
- [ ] `wiki.get_backlinks()` resolves `[[UUID]]` syntax via reverse index (not O(N²) scan)
- [ ] `wiki.expire_drafts()` deletes draft pages older than `ttl_days`
- [ ] All writes use `filelock` with strict lock ordering (index lock before page locks)
- [ ] Unit tests: 90%+ coverage for CRUD, concurrency stress test (10 parallel writers, 0 corruption)

### Goal 2: PageIndex Routing Layer (Phase 1a)
**Objective:** Implement `PageIndex` with JSON tree, LLM-based routing, hierarchical partitioning.

**Acceptance Criteria:**
- [ ] `index.json` schema validates against Pydantic model with `sub_index_path` support
- [ ] `pageindex.route(query)` returns ranked `node_id` list with confidence scores
- [ ] Routing latency 1–3s (documented, not a regression target)
- [ ] `pageindex.rebuild(wiki, incremental=True)` updates only changed category
- [ ] Full rebuild available via `vibe memory wiki index rebuild` command
- [ ] Partitioning triggers on `token_threshold` OR `max_nodes_per_index` (whichever first)
- [ ] Unit tests: routing accuracy measured on golden wiki test set (20 pages, 10 queries, human-annotated ground truth)

### Goal 3: Planner Integration (Phase 1a)
**Objective:** Add wiki hint injection to `HybridPlanner` without changing tier logic.

**Acceptance Criteria:**
- [ ] `HybridPlanner` accepts optional `pageindex` param
- [ ] `_keyword_plan()` injects wiki hints alongside existing trace store hints
- [ ] Wiki hint injection times out after 2s; if timeout, skip without error
- [ ] When `tripartite_enabled=false`, planner behavior is byte-for-byte identical
- [ ] All existing planner tests pass
- [ ] Eval suite pass rate does not regress by >2% vs. baseline

### Goal 4: QueryLoop Integration (Phase 1a + 1b)
**Objective:** Wire wiki lifecycle into `QueryLoop` with async extraction support.

**Acceptance Criteria:**
- [ ] `QueryLoop` accepts optional `wiki` param
- [ ] `close()` cancels any pending `_wiki_extract_task` cleanly
- [ ] Phase 1b: `auto_extract=false` by default; when enabled, extraction uses `asyncio.create_task()`
- [ ] Phase 1b: Extraction requires novelty signal + confidence threshold
- [ ] All existing query loop tests pass
- [ ] New integration tests: manual wiki save, async extraction lifecycle

### Goal 5: CLI Commands (Phase 1a)
**Objective:** Add `memory wiki` subcommands.

**Acceptance Criteria:**
- [ ] `vibe memory wiki list [--tag <tag>] [--status draft|verified]`
- [ ] `vibe memory wiki search <query>` — BM25 search
- [ ] `vibe memory wiki show <page_id>` — display page with rendered links
- [ ] `vibe memory wiki create --title "..." --tags a,b,c` — opens `$EDITOR`
- [ ] `vibe memory wiki edit <page_id>` — opens `$EDITOR`
- [ ] `vibe memory wiki index rebuild` — full index rebuild
- [ ] `vibe memory wiki expire` — run draft expiration

### Goal 6: Config Schema (Phase 1a)
**Objective:** Add Pydantic config models.

**Acceptance Criteria:**
- [ ] `WikiConfig`, `PageIndexConfig`, `RLMConfig`, `TripartiteMemoryConfig` Pydantic models added to `vibe/core/config.py`
- [ ] `TripartiteMemoryConfig.enabled` defaults to `False`
- [ ] `WikiConfig.auto_extract` defaults to `False`
- [ ] Environment override: `VIBE_MEMORY__TRIPARTITE_ENABLED=true`

### Goal 7: Shared Memory Database (Phase 1a)
**Objective:** Consolidate SQLite databases.

**Acceptance Criteria:**
- [ ] `~/.vibe/memory/memory.db` created with tables: `sessions`, `evals`, `wiki_chunks`, `chunk_meta`
- [ ] Existing `traces.db` and `evals.db` migrated on first boot (backward compatible)
- [ ] FTS5 virtual table `wiki_chunks` uses `porter` tokenizer
- [ ] Chunk sync: on wiki page edit, delete old chunks + insert new chunks (atomic transaction)

### Goal 8: RLM Engine (Phase 2, Deferred)
**Objective:** Implement `RLMEngine` with declarative tool loop, plan validation, rate limiting.

**Acceptance Criteria:**
- [ ] `rlm_engine.query()` accepts up to 1M characters
- [ ] Context chunked using configurable strategy (fixed, header, semantic)
- [ ] Plan generated via structured output from main LLM
- [ ] Plan validated: JSONSchema + tool whitelist + argument sanitization + acyclic check
- [ ] Sub-LLM calls: max concurrency 4, VRAM-aware, token-bucket rate limiting
- [ ] Per-step timeouts (not per-query), per-chunk retry (max 3)
- [ ] Fallback to truncation if >50% chunks fail
- [ ] **No `eval()`, `exec()`, or arbitrary Python execution**
- [ ] Unit tests: accuracy on standardized 500K-char benchmark document

---

## 9. Evaluation Criteria

### 9.1 Pros of Tripartite System

1. **Human-readable knowledge:** Markdown wiki files are inspectable and editable
2. **Compounding knowledge:** Wiki pages accumulate and interlink over time
3. **Quality-gated curation:** Draft/verified status + contradiction detection prevents hallucination amplification
4. **Additive, not replacement:** Existing trace store, planner, and compactor are unchanged
5. **Deferred complexity:** RLM only activates when usage data justifies it

### 9.2 Cons & Mitigations

| Risk | Mitigation |
|------|------------|
| Hallucination persistence | Quality gates (novelty signal, confidence threshold, contradiction detection) |
| Wiki garbage accumulation | Draft TTL auto-expiration, manual `vibe memory wiki expire` |
| Planner latency regression | PageIndex runs as augmentation (not tier), with 2s timeout guard |
| Concurrent write corruption | `filelock` with strict lock ordering, stress-tested |
| Index rebuild cost | Incremental rebuild by default; full rebuild is manual |
| API cost from auto-extraction | `auto_extract=false` by default; gated by novelty signal |

### 9.3 Regression Gates

| Metric | Baseline | Tripartite Target | Tolerance |
|--------|----------|-------------------|-----------|
| Eval suite pass rate | Baseline scorecard | Same or higher | -2% |
| Planner latency (p50) | ~5ms keyword / ~5ms embedding | Same (PageIndex is augmentation, not tier) | No regression |
| QueryLoop end-to-end latency | Baseline | Same for simple queries | +10% |
| Memory usage (RSS) | Baseline | Same or lower | +10% |
| Disk usage | Baseline | +wiki pages + index.json | +50MB cap |

---

## 10. Testing Strategy

| Test Type | What | How |
|-----------|------|-----|
| Unit tests | CRUD, locking, schema validation | pytest, 90%+ coverage |
| Golden wiki test | Known-good wiki + index; measure routing accuracy | 20 pages, 10 queries, human-annotated ground truth |
| Concurrency torture test | 10 parallel sessions writing same wiki category | threading stress test, 0 corruption |
| Adversarial extraction test | Sessions with hallucinated content | Verify extractor rejects low-confidence / contradictory content |
| Planner regression test | `tripartite_enabled=false` | Byte-for-byte identical behavior vs. baseline |
| RLM benchmark (Phase 2) | Standardized 500K-char document with known answers | Exact-match F1 scoring |

---

## 11. Source References

1. **Recursive Language Models (RLM)**
   - *Recursive Language Models* (Alex L. Zhang, Tim Kraska, Omar Khattab, 2026)
   - https://arxiv.org/pdf/2512.24601 | Repo: https://github.com/alexzhang13/rlm

2. **LLM Wiki Pattern**
   - *LLM Wiki* (Andrej Karpathy)
   - https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

3. **PageIndex (Reasoning-based RAG)**
   - *PageIndex: Next-Generation Vectorless, Reasoning-based RAG* (Mingtian Zhang, Yu Tang)
   - https://github.com/VectifyAI/PageIndex | Blog: https://pageindex.ai/blog/pageindex-intro

---

## 12. Appendix: Config Schema

```python
# vibe/core/config.py additions

class WikiConfig(BaseModel):
    auto_extract: bool = False        # CHANGED: default false
    base_path: str = "~/.vibe/wiki"
    extraction_prompt: str | None = None  # Custom prompt template
    novelty_threshold: float = 0.5   # Min novelty signal to trigger extraction
    confidence_threshold: float = 0.8  # Min extractor LLM confidence

class PageIndexConfig(BaseModel):
    index_path: str = "~/.vibe/memory/index.json"
    rebuild_on_change: bool = True
    max_nodes_per_index: int = 100
    token_threshold: int = 4000
    routing_timeout_seconds: float = 2.0  # Timeout for wiki hint injection

class RLMConfig(BaseModel):
    enabled: bool = False           # Deferred to Phase 2
    sub_llm_model: str = "default"  # References model name from config, not vendor ID
    max_chunk_size: int = 50000
    max_concurrency: int = 4
    timeout_seconds: float = 60.0
    chunking_strategy: str = "header"
    rate_limit_rpm: int = 60
    rate_limit_tpm: int = 100000

class TripartiteMemoryConfig(BaseModel):
    enabled: bool = False
    wiki: WikiConfig = Field(default_factory=WikiConfig)
    pageindex: PageIndexConfig = Field(default_factory=PageIndexConfig)
    rlm: RLMConfig = Field(default_factory=RLMConfig)

class VibeConfig(BaseSettings):
    # ... existing fields ...
    memory: TripartiteMemoryConfig = Field(default_factory=TripartiteMemoryConfig)
```

---

*End of Design Document v3*
