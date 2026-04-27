# Vibe Agent: Tripartite Memory System — Architectural Design Review

As requested, I have reviewed the `TRIPARTITE_MEMORY_DESIGN.md` in the context of the existing system architecture detailed in `MEMORY_DESIGN.md`. 

While the shift toward a vectorless, reasoning-based Tripartite system (LLM Wiki + PageIndex + RLM) is innovative and solves long-context limitations, the current design document contains severe contradictions and feasibility flaws.

Here is a structured critique of the design document.

---

### 1. Backward Compatibility & Contradictions

**CRITICAL | The "Preserved Behavior" vs. "Dependency Deletion" Contradiction**
*   **Issue:** Section 7.4 states that backward compatibility is preserved via a `memory.tripartite_enabled: bool = False` config flag. However, Goal 5 explicitly mandates removing the `fastText` dependency, removing `numpy` from embeddings, and deleting the embedding tier from `HybridPlanner`. Section 7.2 dictates removing the `session_embeddings` table. 
*   **Impact:** If you delete the embedding libraries and the `session_embeddings` infrastructure from the codebase, the existing `trace_store` vector search and `HybridPlanner` embedding tier *cannot function* when `tripartite_enabled` is set to `False`. 
*   **Resolution:** You must either (A) keep `fasttext`/`numpy` in the codebase for the fallback mode, or (B) explicitly declare that vector-search fallback is deprecated and removed entirely, abandoning backward compatibility for `get_similar_sessions_vector()`.

**HIGH | Semantic Search vs. No Embeddings Contradiction**
*   **Issue:** Goal 4 mentions using `sqlite-vec` semantic search as an optional pre-filter. However, Goal 5 removes all embedding dependencies. 
*   **Impact:** `sqlite-vec` requires a client-side embedding model (like `fastText` or an API) to convert the user's text query into a vector before performing the SQL search. You cannot have semantic search without an embedding generation pipeline.

### 2. Feasibility of Implementation Goals

**CRITICAL | PageIndex Latency Expectations**
*   **Issue:** Section 4.6 estimates `PageIndex` LLM reasoning will take "~50ms", and Goal 8's Acceptance Criteria requires routing to complete in `<100ms`. 
*   **Impact:** Using an LLM to ingest a JSON tree and reason about it involves network I/O (or GPU compute), prompt processing, and text generation. Time-To-First-Token (TTFT) alone for an LLM is typically 300ms–1000ms. Hitting <100ms for an LLM-based routing tier is physically impossible with current models, whereas the existing `fastText` local embeddings easily achieved ~5ms.
*   **Resolution:** Change the latency target to ~1-3s for LLM-based routing, or implement a hybrid where keyword/BM25 filtering happens first to narrow the index tree before LLM reasoning.

**CRITICAL | RLM Sandboxing Security (RCE Risk)**
*   **Issue:** Goal 3 requires the `RLMEngine` REPL to be "sandboxed (restricted builtins... no open())". 
*   **Impact:** Implementing a secure Python sandbox in pure Python using `eval()` or `exec()` with restricted builtins is a well-documented anti-pattern. It is trivially bypassed via Python object introspection (e.g., `().__class__.__base__.__subclasses__()`). Allowing an LLM to generate and execute Python locally on the user's machine introduces a massive Remote Code Execution (RCE) vulnerability. 
*   **Resolution:** The REPL must be executed in an isolated environment (e.g., Docker, gVisor, or WASM), or you must drop the Python REPL entirely and use a rigid, declarative JSON-based tool-calling loop instead of free-form Python execution.

### 3. Performance & Cost Concerns

**CRITICAL | Local GPU Memory (VRAM) Exhaustion during RLM**
*   **Issue:** Section 6.2 describes processing a 500K character document by splitting it into 10 chunks of 50K and executing 10 *parallel* `llm_query_async()` calls against a local `Qwen3-8B` model.
*   **Impact:** Launching 10 concurrent requests of ~12K tokens each against a local 8B model will cause an immediate Out-Of-Memory (OOM) error on consumer GPUs due to KV cache explosion. If the local inference server (like Ollama) queues them sequentially to save VRAM, it will take several minutes, violating the `<30s` acceptance criteria in Goal 8.
*   **Resolution:** Limit concurrency based on available VRAM, or use a smaller sub-model (e.g., 1B parameter flash model) for chunk processing, or process chunks sequentially with streaming.

**HIGH | API Rate Limits for Frontier Models**
*   **Issue:** If using Claude Haiku or GPT-4o-mini for the RLM sub-calls, firing 10–20 parallel high-context requests instantly spikes Tokens-Per-Minute (TPM) and Requests-Per-Minute (RPM).
*   **Impact:** Users on Tier 1 or Tier 2 API plans will hit rate limits instantly, causing the entire query to fail. 
*   **Resolution:** Implement token-bucket rate limiting and request batching. Use a single sub-LLM call with multiple chunks when possible, or fall back to sequential processing with backoff.

### 4. Architectural Soundness & Missing Edge Cases

**HIGH | Concurrency and Race Conditions on Wiki/Index**
*   **Issue:** The design ignores concurrency for Wiki operations. 
*   **Impact:** If multiple agent sessions or parallel terminal tabs run simultaneously and attempt to extract knowledge at the end of their sessions, they will race to update `index.json` and `wiki_page.md` files. This will result in corrupted markdown files and malformed JSON indexes. 
*   **Resolution:** You need an explicit file-locking mechanism (e.g., `filelock` package) for all writes to the `LLMWiki` and `PageIndex`.

**MEDIUM | Synchronous Extraction Overhead**
*   **Issue:** Section 6.1 shows Wiki extraction happening at the end of `QueryLoop`.
*   **Impact:** Prompting an LLM to extract facts and update the Wiki at the end of every conversation will add significant latency before the CLI releases the user's terminal. This process should be decoupled and placed in a background asynchronous worker or daemon.
*   **Resolution:** Make wiki extraction asynchronous (fire-and-forget) or schedule it via a background cron job. Only block on extraction if the user explicitly runs `vibe memory save`.

**MEDIUM | PageIndex Context Window Overflow**
*   **Issue:** As the Wiki grows, `index.json` grows linearly.
*   **Impact:** The design lacks a strategy for what happens when `index.json` exceeds the context window or token budget of the routing LLM. You will eventually need a strategy to chunk or hierarchically search the `PageIndex` itself.
*   **Resolution:** Implement hierarchical index chunking — when the index exceeds a threshold, create sub-indexes by tag/category and route through a two-level index tree.

**MEDIUM | Missing RLM Failure Recovery**
*   **Issue:** The design does not specify what happens when the RLM sub-LLM calls fail (network error, rate limit, model error).
*   **Impact:** A single failed chunk could corrupt the final answer or cause the entire query to hang.
*   **Resolution:** Add per-chunk retry logic with exponential backoff, and a fallback to direct truncation if RLM fails after N retries.

**LOW | Wiki Page ID Collisions**
*   **Issue:** The schema shows `id: doc_004` but does not specify ID generation strategy.
*   **Impact:** Manual ID assignment or simple incrementing will collide in multi-session or multi-user scenarios.
*   **Resolution:** Use UUIDs or deterministic hashes (e.g., `hashlib.sha256(title + date)[:8]`) for page IDs.

---

*End of Gemini CLI Review*
