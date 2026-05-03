# Phase 3.1 Implementation Plan: Vector Search Upgrade (PageIndex)

## Objective
Replace fastText 50-dim embeddings with sentence-transformers (all-MiniLM-L6-v2, 384-dim) in PageIndex, HybridPlanner, and the shared embedding module. Maintain backward compatibility and singleton model pattern.

## Files to Modify

### 1. vibe/harness/embeddings.py (FOUNDATION)
**Current state:** fastText-only (`cc.en.50.bin`), returns None if fasttext not installed.
**Changes:**
- Try sentence-transformers first (all-MiniLM-L6-v2, 384-dim)
- Fall back to fastText if available (backward compat)
- If neither available, return None (existing behavior — callers fall back to keyword)
- Keep singleton model pattern
- Update `cosine_similarity()` to handle any dimension

### 2. vibe/memory/page_index.py (CONSUMER)
**Current state:** Uses fastText via `embeddings.get_embedding()`, expects 50-dim vectors.
**Changes:**
- No direct embedding model reference — calls `embeddings.get_embedding()`
- Update similarity threshold from 0.5 (fastText) to 0.65 (MiniLM) — empirically determined
- Update any hardcoded dimension checks (50 → remove or make dynamic)
- `_find_existing_page()` — keep title-overlap (Jaccard) as primary, vector as secondary

### 3. vibe/harness/planner.py (CONSUMER)
**Current state:** HybridPlanner embedding tier uses fastText, confidence threshold 0.6.
**Changes:**
- No direct model changes — uses `embeddings.get_embedding()` and `cosine_similarity()`
- Update embedding tier confidence threshold from 0.6 → 0.75 (MiniLM is more discriminative)
- Keep keyword tier as primary fast-path

### 4. vibe/memory/__init__.py (PROTOCOL)
**Changes:**
- Add `VectorIndex` protocol class (abstract base for future backend swaps)
- Export it

### 5. Tests
**tests/test_page_index.py:**
- Update mock embeddings to return 384-dim vectors
- Add paraphrase query test ("how to write rust" vs "rust programming guide")

**tests/test_planner.py:**
- Update embedding confidence threshold assertions (0.6 → 0.75)
- Verify keyword tier still triggers on exact matches

## Interface Changes

### New: VectorIndex Protocol
```python
class VectorIndex(Protocol):
    def index(self, doc_id: str, text: str) -> None: ...
    def search(self, query: str, limit: int = 5) -> list[tuple[str, float]]: ...
    def delete(self, doc_id: str) -> None: ...
```

### Modified: embeddings.get_embedding()
- Same signature, same return type (list[float] | None)
- Same singleton behavior
- Different model backend (MiniLM instead of fastText)
- Dimension changes from 50 → 384 (transparent to callers)

## Backward Compatibility
- If sentence-transformers not installed → try fastText → return None
- All callers already handle None (fallback to keyword search)
- No config file changes needed
- No database schema changes (embeddings stored as bytes, dimension-agnostic)

## Rollback Plan
- Revert `vibe/harness/embeddings.py` to fastText-only
- Thresholds in page_index.py and planner.py stay compatible (lower thresholds work with both)

## Test Strategy
1. Unit: Mock `get_embedding()` returning 384-dim vectors, verify PageIndex similarity
2. Integration: Real MiniLM model loaded, verify paraphrase queries match
3. Regression: Full test suite (944 tests) must all pass
4. Edge: Model not installed → graceful fallback to keyword

## Implementation Order
1. `embeddings.py` — foundation, must work first
2. `page_index.py` — update thresholds, verify with tests
3. `planner.py` — update threshold, verify with tests
4. `__init__.py` — add VectorIndex protocol
5. Run full test suite
6. Gemini CLI review
7. Fix issues
8. Commit
