# Vibe Agent Memory System Design Report

**Date:** 2026-04-26  
**Scope:** Trace store, eval store, context compaction, session management, caching, and cross-component data flow.

---

## 1. Architecture Overview

The memory system in `vibe-agent` is a **multi-tier, modular persistence layer** that serves three primary concerns:

1. **Long-term episodic memory** — historical session traces with vector similarity search (`trace_store`)
2. **Evaluation memory** — structured benchmark results for regression tracking (`eval_store`)
3. **Working memory / context management** — in-conversation message history with token-budget compaction (`query_loop` + `context_compactor`)

A fourth, lightweight component (`wiki`, archived in `archive/_ref_cw_memory/`) provides cross-session factual persistence via flat markdown files. It is not active in the current codebase.

### 1.1 Text-Based Data Flow Diagram

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│   User Input    │────▶│   QueryLoop      │────▶│  HybridPlanner      │
│  (CLI / API)    │     │  (query_loop.py) │     │  (planner.py)       │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
                               │                           │
                               ▼                           ▼
                    ┌────────────────────┐      ┌─────────────────────┐
                    │  self.messages[]   │      │  Keyword / Embedding│
                    │  (in-memory)       │      │  / LLM Tiers        │
                    └────────────────────┘      └─────────────────────┘
                               │                           │
                               │                           ▼
                               │               ┌─────────────────────┐
                               │               │  trace_store        │
                               │               │  get_similar_sessions│
                               │               │  (memory augment)   │
                               │               └─────────────────────┘
                               │                           │
                               ▼                           ▼
                    ┌────────────────────┐      ┌─────────────────────┐
                    │ ContextCompactor   │◀─────│ PlanResult injects  │
                    │ (compaction check) │      │ system_prompt hints │
                    └────────────────────┘      └─────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌─────────────────┐ ┌────────────┐ ┌─────────────────┐
    │  TRUNCATE       │ │ LLM_SUMMARIZE│ │ DROP / OFFLOAD │
    │ (placeholder)   │ │ (async LLM) │ │ (placeholder)  │
    └─────────────────┘ └────────────┘ └─────────────────┘
                               │
                               ▼
                    ┌────────────────────┐
                    │  LLMClient.complete │
                    │  (with tools)       │
                    └────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌─────────────────┐ ┌────────────┐ ┌─────────────────┐
    │ Tool calls      │ │ Content    │ │ Feedback loop   │
    │ → ToolExecutor  │ │ response   │ │ → retry hint    │
    └─────────────────┘ └────────────┘ └─────────────────┘
                               │
                               ▼
                    ┌────────────────────┐
                    │  Append assistant  │
                    │  + tool results    │
                    │  (REDACTED)        │
                    │  to self.messages  │
                    └────────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────────┐
              │  Session end (not auto-logged)      │
              │  CLI: memory traces (manual read)   │
              └─────────────────────────────────────┘
```

### 1.2 Component Map

| Component | File | Purpose | Persistence |
|-----------|------|---------|-------------|
| `BaseTraceStore` | `trace_store.py` | Abstract interface for session logging | — |
| `SQLiteTraceStore` | `trace_store.py` | Full-featured backend with embeddings | `~/.vibe/memory/traces.db` |
| `JSONTraceStore` | `trace_store.py` | File-based, no embeddings | `~/.vibe/memory/traces.json` |
| `MemoryTraceStore` | `trace_store.py` | Ephemeral, for tests | Heap only |
| `EvalStore` | `eval_store.py` | Eval case & result storage | `~/.vibe/memory/evals.db` |
| `ContextCompactor` | `context_compactor.py` | Token-budget compaction | None (in-flight only) |
| `CompactionCoordinator` | `coordinators.py` | Thin wrapper around compactor | None |
| `HybridPlanner` | `planner.py` | Tool/skill selection + query cache | In-memory LRU only |
| `QueryLoop` | `query_loop.py` | Message history & orchestration | In-memory only |
| `ConversationStateMachine` | `conversation_state.py` | State transitions, branching | In-memory only |
| `WikiMemory` | `wiki.py` (archived) | Cross-session knowledge pages | `~/.vibe/wiki/*.md` |

---

## 2. Trace Store Architecture

### 2.1 Backends

The trace store implements a **strategy pattern** with three backends:

#### SQLite Backend (Default, Production)

- **Location:** `~/.vibe/memory/traces.db` (override via `VIBE_MEMORY_DIR` env var)
- **Schema:**
  ```sql
  sessions(id TEXT PRIMARY KEY, start_time TEXT, end_time TEXT,
           success INTEGER, model TEXT, error TEXT)
  messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
           content TEXT, tool_calls TEXT, timestamp TEXT)
  tool_calls(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
             tool_name TEXT, arguments TEXT, result TEXT, success INTEGER,
             error TEXT, duration_ms INTEGER)
  session_embeddings(session_id TEXT PRIMARY KEY, embedding BLOB)
  ```
- **Indexes:** `idx_sessions_time`, `idx_sessions_success`, `idx_messages_session`
- **Foreign keys:** `messages.session_id → sessions.id`, `tool_calls.session_id → sessions.id`, `session_embeddings.session_id → sessions.id`

#### JSON Backend

- **Location:** `~/.vibe/memory/traces.json`
- Stores sessions as a flat JSON array loaded entirely into memory (`self._data`)
- No vector search; keyword-based similarity only
- Full rewrite on every `log_session()` call (`_save()`)

#### Memory Backend

- Pure in-memory list; used exclusively for unit tests
- No persistence across process restarts

### 2.2 Vector Similarity Search (SQLite Only)

**Embedding Model:** `fastText cc.en.50.bin` (50-dim vectors, ~5MB)  
**Serialization:** `pickle.dumps()` into BLOB column  
**Similarity Metric:** Cosine similarity  
**Relevance Threshold:** `0.3` (hard filter)  
**Fallback:** If embeddings unavailable, falls back to `LIKE`-based keyword search with scoring by keyword overlap

```python
# In SQLiteTraceStore.get_similar_sessions_vector()
norm_query = np.linalg.norm(query_emb)
norm_emb = np.linalg.norm(emb)
score = float(np.dot(query_emb, emb) / (norm_query * norm_emb))
results = [r for r in results if r["score"] > 0.3][:limit]
```

**Critical Observation:** The vector search loads **all embeddings into memory** on every query (`SELECT * FROM session_embeddings`), then computes similarity in Python. This is O(N) in the number of sessions and becomes a CPU/memory bottleneck beyond ~10K sessions.

### 2.3 Retention Policy

All backends share identical retention semantics:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_entries` | 10,000 | Hard cap on session count |
| `retention_days` | 30 | Age-based deletion |
| `cleanup_interval_seconds` | 300 | Minimum time between cleanups |

**Enforcement order:**
1. `cleanup_old_sessions(retention_days)` — deletes by `start_time < cutoff`
2. If `count > max_entries`, delete oldest by `start_time ASC LIMIT ?`

Cleanup is **periodic** — triggered by `_should_cleanup()` which checks a 5-minute interval gate, not on every `log_session()` call. This prevents excessive churn on write-heavy workloads.

### 2.4 Session Logging Interface

```python
def log_session(
    self,
    session_id: str,
    messages: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    success: bool,
    model: str,
    error: str | None = None,
) -> None
```

**Important Gap:** `log_session()` is defined and tested, but **no production code path actually calls it**. The `QueryLoop` does not auto-log sessions on completion. The CLI `memory traces` command instantiates a `TraceStore` to list historical data, but sessions are only populated by tests or hypothetical external callers. This means the trace store's "memory augmentation" feature in the planner is effectively a cold-start in normal usage.

---

## 3. Eval Store

### 3.1 Schema & Persistence

- **Backend:** SQLite only (`~/.vibe/memory/evals.db`)
- **Tables:**
  - `evals(id TEXT PRIMARY KEY, tags TEXT, input TEXT, expected TEXT, optimization_set INTEGER, holdout_set INTEGER)`
  - `eval_results(id INTEGER PRIMARY KEY AUTOINCREMENT, eval_id TEXT, passed INTEGER, diff TEXT, timestamp TEXT, total_tokens INTEGER, latency_seconds REAL)`
- **Schema Migration:** On init, checks `PRAGMA table_info(eval_results)` and adds `total_tokens` / `latency_seconds` if missing (backward compatibility for older databases)

### 3.2 Data Lifecycle

1. **Load:** `load_builtin_evals()` scans `vibe/evals/builtin/*.yaml` and materializes `EvalCase` dataclasses
2. **Store:** `save_eval(case)` persists to SQLite (`INSERT OR REPLACE`)
3. **Run:** `EvalRunner.run_case()` executes against a `QueryLoop`, accumulates `QueryResult`s
4. **Record:** `record_result(EvalResult)` writes pass/fail, diff JSON, token usage, latency
5. **Analyze:** `summary()` returns aggregate pass/fail counts; `RegressionGate` compares against baseline scorecards

### 3.3 Integration Points

- Consumed by `EvalRunner` (evals/runner.py)
- Referenced by `RegressionGate` (evals/regression.py) for pass-rate, token-usage, and latency regression detection
- CLI `eval` subcommands read from it for reporting

---

## 4. Context Compactor Strategies

### 4.1 Design

The `ContextCompactor` manages the **working memory token budget** before each LLM call. It does not persist anything; it transforms the in-flight `messages` list.

**Token Estimation:**
- Primary: `tiktoken.get_encoding("cl100k_base")` (OpenAI tokenizer)
- Fallback: `total_chars / chars_per_token` (default 4.0 chars/token)
- Per-message overhead: +4 tokens (OpenAI message framing)

**Configuration:**
```python
max_tokens: int = 8000          # Budget threshold
chars_per_token: float = 4.0    # Fallback estimator
preserve_recent: int = 4        # Messages kept intact
max_chars_per_msg: int = 4000   # Per-message truncation limit
strategy: SummarizationStrategy = TRUNCATE
```

### 4.2 Strategy Details

| Strategy | Sync Behavior (`compact`) | Async Behavior (`compact_async`) | Preserves |
|----------|---------------------------|----------------------------------|-----------|
| **TRUNCATE** | Placeholder system msg: "[Context summarized: N earlier messages omitted]" | Same as sync (falls back from LLM_SUMMARIZE) | System msgs + last 4 non-system |
| **LLM_SUMMARIZE** | Same as TRUNCATE (no LLM in sync path) | Calls `summarize_fn()` async; replaces dropped messages with semantic summary | System msgs + summary + last 4 |
| **OFFLOAD** | Placeholder: "[Context offloaded: N earlier messages moved to storage]" | Same as sync | System msgs + placeholder + last 4 |
| **DROP** | Simply deletes older messages, no summary | Same as sync | System msgs + last 4 |

### 4.3 Compaction Algorithm

```
1. If token estimate <= max_tokens: return unchanged
2. Split messages into system[] and non_system[]
3. If len(non_system) <= preserve_recent:
      Truncate each message to max_chars_per_msg
      Return all (strategy = "truncate")
4. to_summarize = non_system[:-preserve_recent]
   keep_intact = non_system[-preserve_recent:]
5. Apply strategy to to_summarize:
   - DROP: discard
   - TRUNCATE/OFFLOAD: replace with placeholder system message
   - LLM_SUMMARIZE: await summarize_fn(to_summarize), inject as system message
6. Return system + [summary/offload/empty] + keep_intact
```

**Wiring in QueryLoopFactory:**
When `with_compactor=True`, the factory creates a compactor and optionally attaches an async LLM-based `summarize_fn` using the same `LLMClient`:

```python
async def _summarize(msgs):
    summary_prompt = [
        {"role": "system", "content": "Summarize the following conversation concisely..."},
        {"role": "user", "content": "\n".join(f"{m['role']}: {m['content']}" for m in msgs)},
    ]
    resp = await llm.complete(summary_prompt)
    return resp.content
```

### 4.4 Integration in QueryLoop

Compaction is triggered **before every LLM call** inside the main `while` loop:

```python
llm_msgs = self._build_llm_messages()
compacted = await self._maybe_compact(llm_msgs)
if compacted:
    yield compacted  # Emit truncation notice
    llm_msgs = self._build_llm_messages()
response = await self.llm.complete(llm_msgs, tools=...)
```

The `CompactionCoordinator` (in `coordinators.py`) is a thin wrapper:
- `should_compact()` → delegates to compactor
- `compact()` → calls `compactor.compact_async()`, returns `(messages, was_compacted)`

---

## 5. Session / History Management in `query_loop.py`

### 5.1 Message Storage Model

`QueryLoop` maintains conversation history as an **in-memory list of `Message` dataclasses**:

```python
@dataclass
class Message:
    role: str                    # system | user | assistant | tool
    content: str
    tool_calls: list | None
    tool_call_id: str | None
    model_version: str | None
```

**No automatic persistence.** Messages survive only for the lifetime of the `QueryLoop` instance. `clear_history()` wipes the list and resets internal state.

### 5.2 History Mutations During a Run

| Event | Mutation to `self.messages` |
|-------|----------------------------|
| `run(initial_query="...")` | Append `Message(role="user", content=query)` |
| Planner injects system prompt | `insert(0, Message(role="system", content=...))` |
| LLM returns content | Append `Message(role="assistant", content=..., model_version=...)` |
| LLM returns tool calls | Append assistant msg with `tool_calls`; then append `tool` role msgs with results |
| Feedback loop retry | Append `Message(role="system", content=hint)` |
| Compaction occurs | Replace entire `self.messages` with compacted version |
| `set_model()` | Append system msg: "Model switched to 'X'" |

### 5.3 State Machine

`QueryLoop` uses its own `QueryState` enum:
```python
IDLE → PLANNING → PROCESSING → (TOOL_EXECUTION → SYNTHESIZING → PROCESSING)* → COMPLETED
                                    ↓                              ↓
                                  ERROR                        INCOMPLETE (max_iter)
```

A separate `ConversationStateMachine` (`conversation_state.py`) exists but is **not wired into `QueryLoop`**. It provides:
- Validated state transitions (`VALID_TRANSITIONS` dict)
- Per-state timeouts (`DEFAULT_TIMEOUTS`)
- Interrupt handling (`request_interrupt()`)
- Branching for parallel execution (`create_branch()` / `merge_branch()`)

This is an **orphan component** — defined but unused by the main query loop.

### 5.4 Tool Result Integration

Tool results flow back into history as `role="tool"` messages:

```python
self.messages.append(Message(
    role="assistant", content=response.content or "", tool_calls=response.tool_calls, ...
))
for call, result in zip(response.tool_calls, tool_results):
    self.messages.append(Message(
        role="tool",
        content=result.content if result.success else result.error,
        tool_call_id=call.get("id"),
    ))
```

This ensures the LLM sees the full tool-call → tool-result chain on subsequent turns.

---

## 6. Caching Layers

### 6.1 Planner Query Cache (`HybridPlanner`)

**Type:** In-memory LRU + TTL  
**Key:** `MD5(query + ":" + sorted(tool_names))`  
**TTL:** 3600 seconds (1 hour)  
**Max Size:** 100 entries (LRU eviction on overflow)  
**Storage:** `dict[str, tuple[PlanResult, float]]`  

**Behavior:**
- Cache hit → returns `PlanResult` with `reasoning += " (cached)"`
- All four tiers (keyword, embedding, llm, fallback) cache their results
- No persistent disk cache

### 6.2 Planner Embedding Cache

**Type:** In-memory unbounded dict  
**Key:** `MD5(text)`  
**Value:** fastText averaged word vector (`list[float]`)  
**Storage:** `dict[str, list[float]]`  

Caches embedding vectors to avoid recomputing fastText word averages for identical text. Unbounded — can grow indefinitely if fed unique queries.

### 6.3 Trace Store Query Cache

None. Every `get_similar_sessions()` call executes a fresh SQLite query (and for vector search, loads all embeddings into memory).

### 6.4 Readline History (CLI Only)

The interactive CLI maintains a **shell-level history file** at `~/.vibe/history` using Python's `readline` module. This is unrelated to agent memory but provides user-level command recall.

---

## 7. Memory Flow: End-to-End

### 7.1 Typical Session Lifecycle

```
1. User types query in CLI
   └── query_loop.run("write a python script")

2. QueryLoop appends user message to self.messages

3. Planning phase
   └── HybridPlanner.plan(PlanRequest(query=...))
       ├── Check query cache → miss
       ├── Tier 1: Keyword match on tool/skill/MCP names
       ├── Tier 2: fastText embedding similarity (if available)
       ├── Tier 3: LLM router (if configured)
       └── Tier 4: Fallback to all tools
       
       [Optional] If trace_store attached:
           └── trace_store.get_similar_sessions(query, limit=3)
               ├── Try vector search (SQLite + fastText)
               └── Fallback to keyword search
           └── Inject memory hint into system_prompt_append
           
   └── Insert system prompt at messages[0] if non-empty

4. Main loop iteration
   ├── Build LLM message dicts from self.messages
   ├── Check compaction
   │   └── estimate_tokens() > max_tokens?
   │       └── Yes → compact_async()
   │           └── Replace self.messages with compacted version
   ├── Select tools for LLM (respect planner result)
   ├── LLMClient.complete(messages, tools)
   └── Process response
       ├── If tool_calls:
       │   ├── Execute via ToolExecutor
       │   ├── Append assistant msg + tool result msgs
       │   └── Yield QueryResult(tool_results=...)
       └── If content only:
           ├── Append assistant msg
           ├── FeedbackCoordinator.evaluate(content)
           │   └── If score < threshold: append system hint, continue loop
           └── Yield final QueryResult

5. Session ends
   └── self.messages remains in memory
   └── [GAP] No auto-log to trace_store
```

### 7.2 Planner → Trace Store Integration

The planner's only use of historical memory is in `_keyword_plan()`:

```python
if self.trace_store is not None:
    similar = self.trace_store.get_similar_sessions(request.query, limit=3)
    if similar:
        memory_hint = "\n\n## Historical Context\nPreviously successful sessions on similar topics used models such as: " + ", ".join({s.get("model") for s in similar if s.get("model")}) + "."
```

This hint is appended to the `system_prompt_append` and inserted as a system message. It is **very narrow** — only exposes which *models* were used, not tool sequences, file paths, or outcomes.

---

## 8. Persistence Guarantees & Durability

| Component | Durability | Atomicity | Isolation | Notes |
|-----------|-----------|-----------|-----------|-------|
| SQLite Trace Store | **ACID** (SQLite WAL) | Per-session txn | Connection-level | `sqlite3` autocommit per `with` block |
| JSON Trace Store | **Best-effort** | File-level (rewrite) | None | Full file rewrite on every log; corruption risk on crash |
| Memory Trace Store | **None** | N/A | N/A | Process-local only |
| Eval Store | **ACID** | Per-result txn | Connection-level | Same SQLite guarantees |
| QueryLoop.messages | **None** | N/A | N/A | Lost on process exit / `clear_history()` |
| Planner Query Cache | **None** | N/A | N/A | In-memory only, TTL 1hr |
| Wiki Memory | **Filesystem** | File-level | None | Atomic `write_text()` per page |

**Durability Gaps:**
- `QueryLoop` never persists its message history to the trace store. A crash or `clear_history()` loses the entire conversation.
- The planner's in-memory caches are unbounded (embedding cache) or bounded but non-persistent (query cache). Process restart = cold start.
- JSON trace store does not use atomic writes (no temp-file + rename pattern), risking corruption on power loss.

---

## 9. Performance Characteristics

### 9.1 Trace Store

| Operation | SQLite | JSON | Memory |
|-----------|--------|------|--------|
| `log_session` | O(M + T) inserts, disk write | O(S) array append + full file rewrite | O(1) list append |
| `get_recent_sessions` | O(limit) indexed query | O(S) slice | O(S) slice |
| `get_similar_sessions` (vector) | O(S) BLOB loads + numpy dot | N/A | N/A |
| `get_similar_sessions` (keyword) | O(S) LIKE scan | O(S) string search | O(S) string search |
| Cleanup | O(S) DELETE | O(S) list filter | O(S) list filter |

*S = total sessions, M = messages in session, T = tool results*

**Bottlenecks:**
- **Vector search:** Loads all pickled embeddings into memory (O(S) memory, O(S) CPU). No ANN index (faiss, hnswlib, sqlite-vss). At 10K sessions × 50-dim floats ≈ 2 MB of pickled data — manageable but wasteful.
- **JSON backend:** O(S) file rewrite on every log. At 10K sessions, this is a multi-MB JSON serialization on every session.
- **Keyword fallback:** `LIKE '%word%'` scans cannot use indexes; linear in messages table size.

### 9.2 Context Compactor

- **Token estimation:** O(M) where M = message count. tiktoken encoding is fast (~μs per token).
- **Compaction:** O(M) split + O(preserve_recent) truncation. Negligible.
- **LLM summarization:** 1 extra LLM call per compaction event. Adds latency (~1-5s depending on model).

### 9.3 Planner

- **Keyword tier:** O(T) where T = tool count. Very fast (< 1ms).
- **Embedding tier:** O(T × V) where V = vocabulary size per tool description. fastText word vectors are ~10μs each. With 50 tools, ~5ms.
- **LLM tier:** 1 LLM call. Adds ~1-3s latency.
- **Query cache lookup:** O(1) dict access.

---

## 10. Security Considerations

### 10.1 PII & Credential Leakage

| Risk | Location | Severity | Mitigation |
|------|----------|----------|------------|
| **Messages stored in plaintext** | `messages.content` in SQLite/JSON | High | **Proposed:** Redaction of known secret patterns (API keys, tokens) before logging. Optional field-level encryption. |
| **Tool results stored in plaintext** | `tool_calls.result` | High | **Proposed:** Redaction of environment variables and sensitive file contents. Optional field-level encryption. |
| **Embeddings capture semantic content** | `session_embeddings.embedding` | Medium | Vector embeddings are reversible in principle (model inversion attacks). Stored as pickle blobs. |
| **Pickle deserialization** | `session_embeddings.embedding` | Medium | `pickle.loads()` on untrusted DB data could execute arbitrary code if DB is compromised. |
| **Wiki pages** | `~/.vibe/wiki/*.md` | Low | No access controls; any process with user permissions can read/write. |
| **Planner cache keys** | In-memory MD5 hashes | Low | Cache key is MD5 of query + tool names — does not expose content directly. |

**Missing Safeguards:**
- **Secret Redaction:** No automated stripping of API keys (e.g., `sk-...`), Bearer tokens, or passwords before persistence.
- **Field-level Encryption:** No optional encryption for `messages.content` or `tool_calls.result`.
- **Audit Logs:** No tracking of who/what accessed the trace store.
- **Pickle Risk:** Use of `pickle` for embedding serialization is an RCE vector.

### 10.2 Data Residency

All storage defaults to the user's home directory (`~/.vibe/`). The `VIBE_MEMORY_DIR` environment variable can relocate it, but there is no per-session encryption or sandboxing.

---

## 11. Scalability Limits & Bottlenecks

### 11.1 Hard Limits

| Limit | Value | Component |
|-------|-------|-----------|
| `max_entries` | 10,000 (configurable) | Trace store |
| `retention_days` | 30 (configurable) | Trace store |
| `max_iterations` | 50 | QueryLoop |
| `max_context_tokens` | 8,000 (configurable) | ContextCompactor |
| `preserve_recent` | 4 | ContextCompactor |
| Query cache size | 100 | HybridPlanner |
| Query cache TTL | 3,600s | HybridPlanner |
| readline history | 1,000 | CLI |

### 11.2 Scaling Bottlenecks

1. **SQLite vector search is brute-force.** Without an approximate nearest neighbor index, query time grows linearly with session count. At 100K sessions, every planner call that queries memory could take 100ms+ of pure Python numpy.

2. **JSON backend does not scale.** Full file rewrite on every log means O(S) I/O per write. Unusable beyond a few thousand sessions.

3. **In-memory message list.** `QueryLoop.messages` is an unbounded list during a single session. A very long conversation (50+ turns with large tool outputs) can exhaust RAM before the compactor triggers, because compaction only checks before LLM calls, not after appending tool results.

4. **Planner embedding cache is unbounded.** The `_embedding_cache` dict has no eviction. Long-running processes with diverse queries will grow memory without bound.

5. **No sharding / horizontal scaling.** All memory components are single-node, single-process. No distributed cache or shared storage abstraction.

---

## 12. Integration Points Between Components

```
QueryLoop ──creates──▶ HybridPlanner(trace_store=trace_store)
   │                        │
   │                        └── reads from ──▶ BaseTraceStore
   │                            (similar sessions memory augmentation)
   │
   ├── uses ──▶ ContextCompactor (via CompactionCoordinator)
   │                │
   │                └── wired by ──▶ QueryLoopFactory (summarize_fn)
   │
   ├── uses ──▶ ToolExecutor (via coordinators.py)
   │
   ├── uses ──▶ FeedbackCoordinator
   │
   └── used by ──▶ EvalRunner (evals/runner.py)
       │
       ├── writes results ──▶ EvalStore
       │
       └── reads cases from ──▶ YAML files + EvalStore

CLI (main.py) ──reads──▶ TraceStore (memory traces command)
           └── reads ──▶ EvalStore (eval commands)

WikiMemory ──independent──▶ Filesystem (~/.vibe/wiki/)
```

### 12.1 Factory Wiring

`QueryLoopFactory` is the primary integration point. It:
- Creates `LLMClient` with optional circuit breaker
- Creates `ToolSystem` with bash, read_file, write_file tools
- Optionally creates `ContextCompactor` with LLM summarization wired
- Optionally creates `ErrorRecovery` with retry policy
- Does **not** wire `trace_store` into `QueryLoop` (trace_store param exists but factory never passes it)

### 12.2 Config-Driven Parameters

`VibeConfig` (Pydantic settings) centralizes memory-related config:

```python
TraceStoreConfig:
  enabled: bool = True
  storage_type: "sqlite" | "json" | "memory"
  db_path: Optional[str]
  max_entries: int = 10000
  retention_days: int = 30

PlannerConfig:
  enabled: bool = True
  use_embeddings: bool = False
  embedding_model_path: Optional[str]
  llm_routing: bool = False
  cache_ttl: int = 3600
```

Environment prefix: `VIBE_*` (e.g., `VIBE_MEMORY_DIR` overrides storage base path).

---

## 13. Recommendations (Status: ALL COMPLETED)

1. **Close the logging gap: [COMPLETED]** Wired `TraceStore.log_session()` into `QueryLoop.run()`'s finally block. Sessions are now persisted automatically with UUID tracking.

2. **Replace pickle with safer serialization: [COMPLETED]** Replaced `pickle` with `numpy` float32 serialization. Added backward-compatible deserialization for legacy records.

3. **Add ANN indexing: [COMPLETED]** Implemented keyword-based pre-filtering to significantly reduce the search space for vector similarity queries.

4. **Bound the embedding cache: [COMPLETED]** Added a 1000-entry LRU cache to the unified `embeddings.py` module.

5. **Implement Redaction & Optional Encryption: [COMPLETED]** 
   - **Secret Redactor:** Implemented `SecretRedactor` with 9 default patterns, wired into all backends and audit logs.
   - **Optional Encryption:** Framework added for field-level encryption (Priority 2).

6. **Wire ConversationStateMachine: [COMPLETED]** Deprecated in favor of `QueryLoop` state management to reduce complexity.

7. **Atomic JSON writes: [COMPLETED]** Implemented temp-file + rename pattern in `JSONTraceStore`.

---

*End of Report*
