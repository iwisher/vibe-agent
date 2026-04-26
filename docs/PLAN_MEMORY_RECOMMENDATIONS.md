# Execution Plan: MEMORY_DESIGN.md Recommendations

**Date:** 2026-04-26  
**Scope:** Implement 6 of 7 recommendations from `docs/MEMORY_DESIGN.md`  
**Skipped:** #5 (Encrypt at rest) — marked as future work due to key management complexity  
**Approach:** 3 grouped PRs for reviewability

---

## PR 0: Embedding Unification (BLOCKING — Must Complete First)

### 0.1 Create Shared Embedding Module

**File:** `vibe/harness/embeddings.py` (new)

**Purpose:** Single source of truth for text embeddings. Both Planner and TraceStore use this module, ensuring consistent 50-dim fastText vectors and a single model load.

```python
"""Shared embedding utilities for vibe-agent.

Uses fastText cc.en.50.bin (50-dim vectors, ~5MB) as the standard embedding model.
"""
import hashlib
import os
from typing import Optional

import numpy as np

try:
    import fasttext
except ImportError:
    fasttext = None

# Global singleton — loaded once, shared across components
_EMBEDDING_MODEL: Optional[fasttext.FastText] = None
_EMBEDDING_CACHE: dict[str, list[float]] = {}


def load_model(model_path: Optional[str] = None) -> Optional[fasttext.FastText]:
    """Load fastText model (singleton)."""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL
    if fasttext is None:
        return None
    path = model_path or os.getenv("FASTTEXT_MODEL_PATH", "cc.en.50.bin")
    if not os.path.exists(path):
        return None
    try:
        _EMBEDDING_MODEL = fasttext.load_model(path)
        return _EMBEDDING_MODEL
    except Exception:
        return None


def get_embedding(text: str, model_path: Optional[str] = None) -> Optional[list[float]]:
    """Get 50-dim fastText embedding for text. Returns None if model unavailable."""
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[cache_key]
    
    model = load_model(model_path)
    if model is None:
        return None
    
    # fastText word-level average (same as planner.py)
    words = text.lower().split()
    if not words:
        return None
    vectors = [model.get_word_vector(w) for w in words if w]
    if not vectors:
        return None
    
    avg = np.mean(vectors, axis=0).tolist()
    _EMBEDDING_CACHE[cache_key] = avg
    return avg


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    a_arr = np.array(a)
    b_arr = np.array(b)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))
```

**Tests:**
- `test_shared_embedding_fasttext` — Verify 50-dim output
- `test_shared_embedding_cache` — Verify caching works
- `test_shared_embedding_cosine_similarity` — Verify similarity computation

### 0.2 Migrate TraceStore from MiniLM to fastText

**File:** `vibe/harness/memory/trace_store.py`

**Changes:**
1. Remove `sentence_transformers` import and `SentenceTransformer` usage
2. Import `get_embedding` from `vibe.harness.embeddings`
3. Replace `_get_embedding()` method with call to shared module
4. Handle dimension mismatch: existing 384-dim embeddings in SQLite BLOBs are incompatible

**Migration strategy for existing databases:**
```python
def _get_embedding(self, text: str) -> Any | None:
    """Get embedding using shared fastText module."""
    from vibe.harness.embeddings import get_embedding
    return get_embedding(text)

# In get_similar_sessions_vector(), detect old embeddings:
for row in rows:
    emb = pickle.loads(row["embedding"])
    if len(emb) == 384:
        # Old MiniLM format — re-compute with fastText
        # (requires fetching the original session text)
        continue  # Skip, will be re-indexed on next log_session
```

**Better approach:** On init, check `session_embeddings` table for 384-dim rows and trigger a background re-index. Or simpler: add a `_embedding_version` column and filter by version.

### 0.3 Migrate Planner to Use Shared Module

**File:** `vibe/harness/planner.py`

**Changes:**
1. Remove local `_embedding_cache`, `_embedding_model`, `_init_fasttext()`
2. Import `get_embedding` and `cosine_similarity` from `vibe.harness.embeddings`
3. Remove `np` import at module level (now in shared module)

**Benefits:**
- Single model load (saves ~5MB + no PyTorch)
- Consistent 50-dim vectors across all components
- Shared LRU cache (can be bounded in one place)

---

## PR 1: Persistence Fixes (Critical — Unblocks Memory Augmentation)

### 1.1 Wire TraceStore.log_session() into QueryLoop

**File:** `vibe/core/query_loop.py`

**Change:** In `QueryLoop.run()`, add a `finally` block that calls `trace_store.log_session()` when the loop completes (success, error, or incomplete).

```python
async def run(self, initial_query=None):
    # ... existing code ...
    try:
        # ... main loop ...
    finally:
        self._running = False
        # NEW: Auto-persist session to trace store
        if self.trace_store is not None:
            await self._log_session_to_trace_store()
```

**Session data to capture:**
- `session_id`: UUID4
- `messages`: Full `self.messages` list (as dicts)
- `tool_results`: Extracted from `QueryResult`s yielded during the run
- `success`: True if final state is COMPLETED, False otherwise
- `model`: `self.llm.model`
- `error`: Last error message if any

**Concerns:**
- `log_session()` is synchronous (SQLite writes). In async `run()`, wrap with `asyncio.to_thread()` to avoid blocking the event loop.
- Large sessions (50 turns with big tool outputs) could be multi-MB. Add a size limit (e.g., cap at 1000 messages, truncate tool outputs to 10KB each).

**Tests:**
- `test_trace_store_auto_log` — Verify session appears in trace store after `run()`
- `test_trace_store_no_log_on_clear_history` — `clear_history()` should not trigger logging
- `test_trace_store_log_size_limit` — Large sessions are truncated

### 1.2 Atomic JSON Writes

**File:** `vibe/harness/memory/trace_store.py`

**Change:** In `JSONTraceStore._save()`, use temp-file + rename pattern:

```python
def _save(self) -> None:
    import tempfile, os
    temp_path = self.file_path + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(self._data, f, indent=2)
    os.replace(temp_path, self.file_path)  # Atomic on POSIX
```

**Tests:**
- `test_json_trace_store_atomic_write` — Verify no data loss on crash simulation

---

## PR 2: Performance Fixes

### 2.1 Replace Pickle with NumPy Serialization

**File:** `vibe/harness/memory/trace_store.py`

**Change:** Replace `pickle.dumps()` / `pickle.loads()` with `numpy.save` / `numpy.load` to BLOB:

```python
# OLD:
# pickle.dumps(emb)
# pickle.loads(row["embedding"])

# NEW:
import io
buf = io.BytesIO()
np.save(buf, np.array(emb, dtype=np.float32))
buf.seek(0)
blob = buf.read()

# Load:
buf = io.BytesIO(row["embedding"])
arr = np.load(buf)
emb = arr.tolist()
```

**Migration:** On first read, detect pickle format (starts with `\x80`) and auto-migrate to numpy format. This handles existing databases.

**Tests:**
- `test_sqlite_trace_store_numpy_serialization` — Round-trip test
- `test_sqlite_trace_store_pickle_migration` — Auto-migrate old pickle data

### 2.2 Bound the Embedding Cache

**File:** `vibe/harness/planner.py`

**Change:** Replace unbounded `dict` with `functools.lru_cache` or a bounded dict with LRU eviction:

```python
from functools import lru_cache

class HybridPlanner:
    def __init__(self, ...):
        self._embedding_cache: dict[str, list[float]] = {}
        self._embedding_cache_max_size = 1000  # Configurable
        
    def _get_embedding(self, text: str) -> list[float]:
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        
        # Compute embedding...
        
        # LRU eviction
        if len(self._embedding_cache) >= self._embedding_cache_max_size:
            # Evict oldest (simple: clear half the cache)
            keys = list(self._embedding_cache.keys())
            for k in keys[:len(keys)//2]:
                del self._embedding_cache[k]
        
        self._embedding_cache[cache_key] = result
        return result
```

**Better approach:** Use `cachetools.LRUCache` for proper LRU semantics:

```python
from cachetools import LRUCache
self._embedding_cache = LRUCache(maxsize=1000)
```

**Tests:**
- `test_planner_embedding_cache_lru` — Verify eviction works
- `test_planner_embedding_cache_size_limit` — Verify max size respected

### 2.3 Add ANN Pre-filtering (Pure-Python)

**File:** `vibe/harness/memory/trace_store.py`

**Change:** Before loading all embeddings, do a coarse keyword pre-filter:

```python
def get_similar_sessions_vector(self, query: str, query_emb: list[float], limit: int = 5):
    # Step 1: Coarse keyword filter — only sessions with overlapping keywords
    query_words = set(query.lower().split())
    candidate_ids = []
    for row in self.conn.execute(
        "SELECT session_id, content FROM messages WHERE role = 'user'"
    ):
        msg_words = set(row["content"].lower().split())
        if query_words & msg_words:  # Any overlap
            candidate_ids.append(row["session_id"])
    
    # Step 2: Vector search only on candidates
    if not candidate_ids:
        return []  # No candidates, skip expensive vector load
    
    # Load embeddings for candidates only
    placeholders = ",".join("?" * len(candidate_ids))
    rows = self.conn.execute(
        f"SELECT session_id, embedding FROM session_embeddings WHERE session_id IN ({placeholders})",
        candidate_ids
    )
    # ... compute similarity only on candidates ...
```

**Benefit:** Reduces O(S) to O(C) where C = candidate sessions with keyword overlap. For sparse queries, C << S.

**Tests:**
- `test_sqlite_trace_store_prefilter` — Verify pre-filter reduces loaded embeddings
- `test_sqlite_trace_store_prefilter_no_candidates` — Empty result when no keyword overlap

---

## PR 3: Cleanup & Deprecation

### 3.1 Deprecate ConversationStateMachine

**File:** `vibe/harness/conversation_state.py`

**Change:** Add deprecation warning and mark for removal in v2.0:

```python
import warnings

class ConversationStateMachine:
    def __init__(self, ...):
        warnings.warn(
            "ConversationStateMachine is deprecated and will be removed in v2.0. "
            "QueryLoop now uses its own QueryState enum.",
            DeprecationWarning,
            stacklevel=2,
        )
        # ... rest of init ...
```

**File:** `vibe/core/query_loop.py`

**Change:** Remove the import of `ConversationStateMachine` if it exists (it doesn't currently — the report correctly notes it's an orphan component).

**Tests:**
- `test_conversation_state_machine_deprecation` — Verify warning is raised

### 3.2 Update Tests for New Behavior

**File:** `tests/test_query_loop.py`

**Changes:**
- Add `test_query_loop_logs_session_to_trace_store` — Verify auto-logging
- Add `test_query_loop_trace_store_size_limit` — Verify truncation

**File:** `tests/harness/memory/test_trace_store.py`

**Changes:**
- Add `test_sqlite_trace_store_numpy_embeddings` — Verify numpy serialization
- Add `test_json_trace_store_atomic_write` — Verify atomic writes

---

## Implementation Order

```
Phase 0: PR 0 (Embedding Unification) — BLOCKING
  ├─ 0.1 Create shared embedding module (vibe/harness/embeddings.py)
  ├─ 0.2 Migrate TraceStore from MiniLM to fastText
  ├─ 0.3 Migrate Planner to use shared module
  └─ Tests + Gemini review

Phase A: PR 1 (Persistence)
  ├─ 1.1 Wire TraceStore.log_session() into QueryLoop
  ├─ 1.2 Atomic JSON writes
  └─ Tests + Gemini review

Phase B: PR 2 (Performance)
  ├─ 2.1 Replace pickle with numpy serialization (now single format: 50-dim)
  ├─ 2.2 Bound embedding cache with LRU (in shared module)
  ├─ 2.3 ANN pre-filtering
  └─ Tests + Gemini review

Phase C: PR 3 (Cleanup)
  ├─ 3.1 Deprecate ConversationStateMachine
  ├─ 3.2 Update tests
  └─ Tests + Gemini review
```

**Total estimated work:** ~15 hours across 4 PRs  
**Critical path:** PR 0 (embedding unification) → PR 1 (persistence)  
**Riskiest:** PR 0 (dimension mismatch migration) and PR 2.1 (pickle→numpy with existing DBs)

---

## Rollback Plan

| PR | Rollback Trigger | Action |
|----|-----------------|--------|
| PR 0 | fastText model not found (cc.en.50.bin missing) | Fall back to keyword-only search, log warning |
| PR 0 | Existing 384-dim embeddings cause dimension mismatch | Detect on read, skip old embeddings, re-compute on next write |
| PR 1 | Session logging causes performance regression (>100ms per session) | Revert `finally` block, add feature flag `auto_log_sessions: bool` |
| PR 2.1 | Numpy migration corrupts existing databases | Add `force_pickle: bool` config option for backward compatibility |
| PR 2.2 | LRU cache causes cache thrashing (frequent eviction) | Increase default size or make configurable |
| PR 2.3 | Pre-filtering misses relevant sessions | Add `prefilter_enabled: bool` toggle |
| PR 3 | Deprecation warning breaks downstream consumers | Remove warning, keep class as no-op stub |

---

## Success Criteria

1. **PR 1:** `QueryLoop.run()` produces trace store entries without manual intervention. Memory augmentation in planner returns non-empty results for repeated queries.
2. **PR 2:** Vector search latency improves >50% for databases with >1000 sessions. Embedding cache memory usage stays bounded.
3. **PR 3:** No `ConversationStateMachine` import errors. All existing tests pass.
4. **All PRs:** Test suite stays at >660 passing (no regressions beyond the 11 pre-existing config failures).
