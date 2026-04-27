# Architecture Design Review Report: Tripartite Memory System (v2)

I have reviewed the `TRIPARTITE_MEMORY_DESIGN.md` (v2) against the original review feedback. The revised design represents a significant maturation of the architecture, effectively mitigating the severe security and performance risks present in the previous iteration.

Here is the structured assessment of the v2 design document.

### 1. Issue-by-Issue Resolution Status

| Original Issue | Severity | Status | How it was addressed |
| :--- | :--- | :--- | :--- |
| **"Preserved Behavior" vs. "Dependency Deletion"** | CRITICAL | **FIXED** | The `fastText` dependency was retained but placed behind a `try/except` guard. `HybridPlanner` now safely falls back to it if installed, while `TraceStore` explicitly abandons vector search entirely to simplify episodic logging. |
| **PageIndex Latency Expectations** | CRITICAL | **FIXED** | Latency expectations were realistically adjusted from <100ms to 1-3 seconds. The introduction of a lightweight SQLite FTS5/BM25 pre-filter ensures the LLM isn't flooded with massive contexts for basic lookups. |
| **RLM Sandboxing Security (RCE Risk)** | CRITICAL | **FIXED** | Excellent correction. The highly insecure Python REPL (`eval`/`exec`) was completely removed and replaced with a strict, declarative JSON-based tool-calling loop (`RLMInterpreter`). |
| **Local GPU Memory (VRAM) Exhaustion** | CRITICAL | **FIXED** | Default concurrency was lowered from 10 to 4, and the design now explicitly specifies that parallel sub-LLM calls must be VRAM-aware (e.g., via `nvidia-smi` or API checks). |
| **Semantic Search vs. No Embeddings** | HIGH | **FIXED** | Clarified that semantic search via `sqlite-vec` is strictly an *optional* feature that only activates if the `fastText` dependency is present. The system works with BM25-only by default. |
| **API Rate Limits for Frontier Models** | HIGH | **FIXED** | A `TokenBucket` rate limiter (RPM/TPM) was introduced to gate sub-LLM execution. |
| **Concurrency & Race Conditions** | HIGH | **FIXED** | Explicit integration of the `filelock` package for all Wiki and Index write operations guarantees safety across parallel terminal tabs. |
| **Synchronous Extraction Overhead** | MEDIUM | **FIXED** | Wiki extraction at the end of `QueryLoop` is now explicitly asynchronous and non-blocking via a background thread. |
| **PageIndex Context Window Overflow** | MEDIUM | **FIXED** | Introduced "Hierarchical Index Chunking," partitioning `index.json` into category sub-indexes once it exceeds a 4,000-token threshold. |
| **Missing RLM Failure Recovery** | MEDIUM | **FIXED** | Added per-chunk retry logic with exponential backoff (max 3 retries) and a fallback to direct truncation/summary if >50% of chunks fail. |
| **Wiki Page ID Collisions** | LOW | **FIXED** | ID schema was updated to explicitly require UUID-based generation. |

---

### 2. Remaining Concerns & New Issues

While the architecture is structurally sound, a few operational risks remain that should be monitored during implementation:

1. **NEW | Hidden Resource/Cost Drain from Auto-Extraction:**
   Section 3.5 and the Config Appendix indicate that `auto_extract: true` runs a background LLM thread at the end of *every* session. While non-blocking, triggering an LLM extraction call after every CLI command will silently consume significant API credits or tie up local GPU resources. Consider defaulting this to `false` or batching extraction tasks.
2. **NEW | `sqlite-vec` Portability & Deployment:**
   Relying on `sqlite-vec` for optional semantic search (Goal 4) requires loading native SQLite extensions. Depending on the host OS and Python environment, this can be an installation headache. Ensure the code falls back gracefully to standard BM25 if the `sqlite-vec` module fails to load.
3. **NEW | TraceStore Deprecation Scope:**
   By removing `session_embeddings` from `TraceStore`, users who run with `tripartite_enabled: false` will entirely lose the ability to do semantic similarity searches over past session histories. This is an acceptable design tradeoff, but it constitutes a breaking change for fallback users and must be heavily emphasized in release notes.

---

### 3. Overall Verdict

**Verdict: READY FOR IMPLEMENTATION**

The v2 design successfully addresses all structural, security, and performance blockers identified in the original review. The pivot to a declarative JSON tool loop resolves the critical RCE vulnerability, and the realistic handling of LLM latency and local concurrency demonstrates a mature understanding of agentic constraints. The remaining concerns are primarily operational (cost tuning and deployment logistics) rather than architectural blockers. 

You are clear to proceed to the implementation phase.
