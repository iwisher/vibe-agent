# Phase 3: Vector Search Upgrade + RLM Training Pipeline

## Background

Two independent improvements to the Tripartite Memory System:

1. **Vector Search Upgrade** — `PageIndex._keyword_route()` currently uses word overlap (Jaccard similarity on title/description tokens). It fails for paraphrase queries ("How do I…" vs "Guide to…") and semantically related terms ("currency" vs "money"). Replacing it with `all-MiniLM-L6-v2` embeddings gives dense vector similarity at 22MB model size.

2. **Phase 3b RLM Training Pipeline** — `RLMThresholdAnalyzer.analyze()` returns `should_trigger=True` but nothing acts on it. This phase wires the trigger to an actual LoRA fine-tuning job using `unsloth` (or plain `peft`/`transformers` as fallback), runs it in a background subprocess, and hot-swaps the Ollama model when done.

---

## Open Questions

> [!IMPORTANT]
> **RLM Training Environment**: Does the user have a GPU available (MPS/CUDA)? LoRA fine-tuning on CPU is possible but slow (minutes for small models). The pipeline will auto-detect and use `mps` (Apple Silicon), `cuda`, or `cpu`.

> [!IMPORTANT]
> **Dependency appetite**: `sentence-transformers` (~400MB with model) and `unsloth`/`peft` (~200MB) are heavy optional dependencies. These will be added to `pyproject.toml` as optional extras `[memory]` and `[rlm]` respectively — not required for base install.

> [!WARNING]
> **RLM Phase 3b Scope**: Full LoRA training requires a base model (e.g., `qwen3:1.7b` pulled via Ollama). The pipeline will export training data from the wiki + trace store, run `transformers` PEFT LoRA, save weights, and register with Ollama. The pipeline is intentionally conservative: it requires explicit `rlm.enabled=true` AND the trigger decision fires AND `rlm_model_path` is set.

---

## Proposed Changes

### Feature 1: Vector Search Upgrade

---

#### [NEW] `vibe/memory/vector_index.py`

New module implementing `VectorIndex` — a protocol + concrete `SentenceTransformerIndex` class:

- **Protocol** `VectorIndex`: `encode(texts) → np.ndarray`, `search(query, nodes, top_k) → list[tuple[float, IndexNode]]`
- **`SentenceTransformerIndex`**: lazy-loads `sentence-transformers` model on first call; persists embeddings to a `.npy` cache file next to `index.json`; cosine similarity search over cached embeddings
- **`KeywordIndex`**: wraps existing `_keyword_route` logic as a `VectorIndex` implementation (no-dependency fallback)
- `get_vector_index(model_name, cache_path) → VectorIndex`: factory that returns `SentenceTransformerIndex` if importable, else `KeywordIndex`

---

#### [MODIFY] `vibe/memory/pageindex.py`

- Add `vector_index: VectorIndex | None` attribute (set via `set_vector_index()` or lazily on first `route()` call)
- In `route()`: if vector index available, call `_vector_route()` instead of `_keyword_route()`; LLM route remains the top-tier path
- `_vector_route(query, root)`: flatten all leaf nodes → encode query → cosine similarity against cached embeddings → return top-5 as `IndexNode` with `confidence` scores
- `rebuild()`: after rebuilding, re-encode all page descriptions and save to embedding cache
- Backward compatible: `PageIndex(llm_client=None)` with no vector index works exactly as before

---

#### [MODIFY] `vibe/core/config.py`

Add `PageIndexConfig.vector_search_enabled: bool = False` and `PageIndexConfig.embedding_model: str = "all-MiniLM-L6-v2"` fields.

---

#### [MODIFY] `vibe/core/query_loop_factory.py`

In `_create_tripartite()`: if `idx_cfg.vector_search_enabled`, instantiate `SentenceTransformerIndex` and wire into `PageIndex` via `pageindex.set_vector_index(vi)`.

---

#### [NEW] `tests/memory/test_vector_index.py`

- Test `KeywordIndex.search()` returns correct results (pure unit test, no ML deps)
- Test `SentenceTransformerIndex` with pytest skip if `sentence-transformers` not installed
- Test `PageIndex._vector_route()` with a mocked `VectorIndex`
- Test `PageIndex.route()` prefers vector over keyword when `vector_index` set
- Test embedding cache is persisted and reloaded

---

#### [MODIFY] `pyproject.toml`

Add optional extra:
```toml
[project.optional-dependencies]
memory = ["sentence-transformers>=2.7.0", "torch>=2.0.0"]
```

---

### Feature 2: Phase 3b RLM Training Pipeline

---

#### [NEW] `vibe/memory/rlm_trainer.py`

Core training orchestration:

- **`RLMTrainingConfig`** dataclass: `base_model`, `output_path`, `dataset_path`, `max_steps`, `lora_r`, `device`
- **`RLMTrainer`** class:
  - `async def prepare_dataset(wiki, trace_store) → Path`: export wiki pages + trace sessions as JSONL conversation pairs to a temp file
  - `async def train(config) → Path`: run LoRA fine-tuning via subprocess (`python -m vibe.memory._rlm_train_worker`); returns path to saved adapter weights
  - `async def register_with_ollama(adapter_path, model_name) → bool`: call Ollama `/api/create` to register the fine-tuned model
  - Error handling: all failures are non-fatal, logged as WARNING

- **`vibe/memory/_rlm_train_worker.py`** (called as subprocess):
  - Uses `peft` + `transformers` (or `unsloth` if available) for LoRA
  - Reads training config from stdin as JSON
  - Writes adapter weights to `output_path`
  - Exits with 0 on success, non-zero on failure

---

#### [MODIFY] `vibe/memory/rlm_analyzer.py`

- Change `RLMTriggerDecision.should_trigger` handling: add `training_callback: Callable | None` field to analyzer
- `async def analyze_and_train(wiki, trace_store, rlm_trainer) → RLMTriggerDecision`: combines analyze + conditional training launch

---

#### [MODIFY] `vibe/core/query_loop.py`

In `_maybe_trigger_rlm()`:
- If `decision.should_trigger` AND `config.rlm.rlm_model_path` is set:
  - Instantiate `RLMTrainer` and call `trainer.train(config)` as a background task
  - Log "RLM training started" at INFO level
- Otherwise: keep existing log-only behavior

---

#### [MODIFY] `vibe/core/config.py`

`RLMConfig` additions:
- `auto_train: bool = False` — must be explicitly enabled
- `base_model: str = "qwen3:1.7b"` — base model for LoRA
- `lora_r: int = 8` — LoRA rank
- `max_train_steps: int = 100` — safety cap
- `training_device: str = "auto"` — "auto", "cpu", "cuda", "mps"
- `ollama_register: bool = True` — register with Ollama after training

---

#### [NEW] `tests/memory/test_rlm_trainer.py`

- Test `prepare_dataset()` exports correct JSONL format with mocked wiki/trace_store
- Test `train()` with mocked subprocess (verify correct args passed)
- Test `register_with_ollama()` with mocked httpx response
- Test full pipeline: analyze → trigger → train (all mocked, no actual ML)
- Test failure cases: subprocess fails → logged, not raised

---

#### [MODIFY] `pyproject.toml`

Add optional extra:
```toml
[project.optional-dependencies]
rlm = ["peft>=0.10.0", "transformers>=4.40.0", "torch>=2.0.0", "datasets>=2.18.0"]
```

---

## Verification Plan

### Automated Tests
```bash
# Run all new and existing memory tests
.venv/bin/python -m pytest tests/memory/ -x --tb=short -q

# Run with sentence-transformers installed (if available)
.venv/bin/python -m pytest tests/memory/test_vector_index.py -v

# Full suite regression
.venv/bin/python -m pytest tests/ -q
```

### Manual Verification
- Vector search: `vibe memory wiki search "monetary policy inflation"` should return relevant pages even if they don't contain those exact words
- RLM pipeline: set `rlm.auto_train=true`, run 10+ sessions, verify trainer subprocess is invoked and adapter weights are saved

---

*Last updated: 2026-04-27*
