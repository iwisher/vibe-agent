# Critique: TRIPARTITE_DESIGN.md

**Reviewer:** Claude (Sonnet 4.6)
**Date:** 2026-04-26
**Subject:** Review of `docs/TRIPARTITE_DESIGN.md` against existing memory system in `docs/MEMORY_DESIGN.md`

---

The proposal has a strong conceptual frame (Index/Storage/Execution as textbook metaphor) and correctly identifies the trace store's biggest weakness — it's retrieval-only, never *written to* by the agent. But the design has several structural problems that should be addressed before Phase 1 starts.

## 1. The acceptance criterion for hierarchical routing is implausible

§4.2.2 + §7.3 (S4): "routing < 500ms for 1000-node index" with iterative LLM drill-down.

If each level requires an LLM call ("Ask LLM: Which top-level categories are relevant?"), depth-4 traversal is **4 sequential LLM calls** = 1.5–4s realistic, not <500ms. Even with the cheapest flash model, you can't beat this serially. Compare to the existing `HybridPlanner` embedding tier (~10ms with fastText) or sqlite-vec (sub-100ms for 10K rows).

The design dismisses vectors as a routing primitive but then promises latency that *only* a vector approach can deliver. Either:
- Drop the latency target and acknowledge routing is a 1–4s operation, or
- Make embeddings the primary routing tier with LLM drill-down as a *re-ranker* over the top-K candidates.

The current §3.1 hedge ("vectors as a fallback or pre-filter") is the right idea but isn't reflected in §4.2.2's algorithm.

## 2. Phase 2 RLM is over-engineered for the stated need

§4.3 builds a tri-layer sandbox (RestrictedPython + AST + `sandbox-exec`) for one stated use case: reading documents larger than the context window. Concerns:

- **`sandbox-exec` is macOS-only** and Apple has marked it deprecated. Linux/CI parity is non-trivial (bubblewrap, firejail, gVisor — none drop in cleanly).
- **RestrictedPython has known bypasses** and is not actively maintained the way it once was. AST verification is a cat-and-mouse game.
- The RLM sandbox must `deny network*` *except* for `llm_query_async()` calls. That conditional egress ACL is non-trivial and not specified.
- Modern context windows (200K–1M tokens) make "document larger than context" rare in practice. For an agent harness whose existing compactor handles 8K-token budgets, this is a steep complexity jump for an edge case.

The same outcome — chunked decomposition with sub-LLM delegation — can be achieved with a tool-calling pattern (`chunk_and_query` tool returning structured results) without exposing arbitrary code execution. Defer Phase 2 entirely, or replace it with a constrained tool, until real usage data shows it's needed.

## 3. Agent-authored wiki has unaddressed hallucination/staleness risks

§4.1 frames the wiki as durable knowledge, but:

- **Hallucinations get persisted and re-cited.** The agent invents a wrong "fact" once → it's written to `database_scaling.md` → next session retrieves it as authoritative. There's no cross-validation step before write.
- **No invalidation story.** If infra changes (sharding replaces read-replicas), how does `[[Database_Scaling]]` get corrected? `last_updated` doesn't capture *correctness over time*.
- **`status: draft|verified`** is mentioned three times with no specification of who flips the bit, when, or why. In practice everything stays `draft` and the field becomes meaningless.
- **Contradictions across pages** are inevitable as the wiki grows. No consistency mechanism is described.
- **`source_session` becomes a dangling reference** after 30 days (trace store retention). A wiki page citing a deleted session has no provenance recovery path.

Mitigations to add to the design:
- Require *citation density* (page must reference ≥N session traces) before promotion past draft
- TTL on draft pages (auto-expire if not re-touched in M sessions)
- Detect contradictions at write time by querying the existing wiki for the same topic
- Use page `id` (`doc_004`) as the link target with title as the rendered label, so renames don't break links

## 4. The document is materially incomplete

Sections that need to exist before this is implementable:

- **§4.3 RLM API**: only the constructor is shown. Where are `execute()`, `load_wiki_file()`, `llm_query_async()` signatures? Error model? Output format?
- **§7 Acceptance Criteria**: only §7.3 (Safety) exists. §7.1 (Correctness) and §7.2 (Performance) are missing entirely.
- **§5 Phases**: bullet list with no time estimates, owners, exit criteria, or rollback plan.
- **No data migration** from `archive/_ref_cw_memory/` `WikiMemory` or coexistence story with the active trace store.
- **No metrics/observability**: how do we know the wiki is helping vs. just adding latency and cost? What counters/spans?
- **No testing strategy**: integration tests, golden wikis, regression detection.

## 5. Integration with the existing harness is hand-wavy

`HybridPlanner` already does memory injection from `trace_store.get_similar_sessions()` (§7.2 of MEMORY_DESIGN). TripartiteMemory adds *another* routing/augmentation system. §4.4.3 just shows `enqueue_curation()` in the finally block — it doesn't address:

- Does PageIndex routing *replace* trace-store-similar-sessions injection in `HybridPlanner._keyword_plan()`, or run alongside it?
- Where in the planner tier ladder does PageIndex sit? Tier 0 (pre-keyword)? Tier 2.5 (between embedding and LLM)?
- The compactor's new `RLM_ARCHIVE` strategy (§4.4.2) — what happens if RLM is disabled or the queue is full? Does it fall back to TRUNCATE silently?
- Concurrent curation: if two `QueryLoop` instances finish simultaneously, who writes `index.json`? Atomic writes prevent corruption but not lost-update.

A "Component Interaction" diagram showing TripartiteMemory + HybridPlanner + trace_store + compactor on one canvas would expose these gaps.

## 6. Curation economics aren't analyzed

Every session triggers:
- 1 LLM call to decide create-vs-update
- 1 LLM call to write/update content
- N LLM calls to update PageIndex categorization

At ~6–8 sessions/hour for a single user that's modest. At eval-suite scale (47 cases × multiple model runs) or in CI, this becomes a real cost line item. The design needs:

- A short cost model: tokens/curation × curations/day
- A "skip curation if session was trivial" heuristic (e.g., no novel tool calls, short response)
- Throttling/coalescing across similar sessions
- A way to disable curation in CI without breaking tests

## 7. Smaller but real issues

| # | Issue | Section |
|---|---|---|
| a | `id: doc_{seq}` requires a global counter — race condition with concurrent sessions writing the wiki | §4.1.1 |
| b | Wiki-links use titles (`[[Database_Scaling]]`); rename = broken link. Use IDs. | §4.1.1 |
| c | Background `CurationQueue` size 100 — what's the backpressure / overflow behavior? | §6 |
| d | No max page size, no max wiki size, no eviction policy | §4.1 |
| e | Secret redaction is good, but `SecretRedactor` is reused from trace store — confirm it covers wiki-specific content (file paths, hostnames as PII) | §4.1.1 |
| f | "Tree depth capped at 4" with no justification — why 4? Why not depth-2 with broader fanout (less LLM hops)? | §4.2.2 |
| g | `flash_model: "qwen3-coder"` hard-coded in sample config — should be model-agnostic per harness convention | §6 |
| h | RLM token budget `500000` is 50× a normal request; needs a kill-switch and per-session ceiling | §6 |

## Recommendations

1. **Decouple Phase 1 from Phase 2.** The wiki + index alone is valuable. Build it, ship it, measure it. Don't gate on a sandboxed Python REPL whose value is unproven.
2. **Make embeddings the primary routing primitive**, with LLM as a re-ranker over top-K. This fits the existing `HybridPlanner` pattern and meets the latency target.
3. **Specify the verification lifecycle.** Without a concrete "who promotes draft → verified" rule, the status field is dead.
4. **Add a "should I curate at all" gate** before enqueuing — most sessions won't have new knowledge worth persisting.
5. **Replace §4.4.3 with a real wiring diagram** that shows TripartiteMemory's relationship to `HybridPlanner` and the existing trace-store memory injection.
6. **Fill in §7.1, §7.2, and the RLM API** before this leaves draft. As written, two of the three pillars (Index, Execution) aren't implementable from the doc alone.

The core idea — *the agent should write what it learns, in a structured form it can re-read* — is right. The architecture around it is more elaborate than the problem requires, and the document underspecifies the parts that would be hardest to get right.

---

# Critique: TRIPARTITE_MEMORY_DESIGN.md (v2)

**Reviewer:** Claude (Sonnet 4.6)
**Date:** 2026-04-26
**Subject:** Review of `docs/TRIPARTITE_MEMORY_DESIGN.md` (v2 revision)

---

v2 is meaningfully better than v1. The declarative RLMInterpreter replacing the Python REPL is the right call (§5.2–5.4). The 1–3s routing latency target is honest (§8 Goal 2). BM25 as the primary pre-filter with optional embeddings is pragmatic (§4.6). `[[page_id]]` links and `filelock` are sensible fixes.

But several structural problems remain — and a few new ones were introduced.

## 1. Auto-extraction will turn the wiki into a hallucination amplifier

§3.5 and §11 specify `auto_extract: true` — every session writes to the wiki automatically. This is the opposite of safe.

The core risk from v1 remains unaddressed: LLMs hallucinate, and now every hallucinated "fact" gets persisted, interlinked, and re-retrieved in future sessions. There is no:
- **Contradiction detection** before write
- **TTL or expiration** on draft pages
- **Quality gate** (e.g., "only extract if tool results contain novel file paths or commands")
- **Human verification path** for the `verified` status field

**Mitigation needed:** `auto_extract` should default to `false`. When enabled, extraction should require a *novelty signal* (new tool outputs, new file paths, explicit user confirmation) and a *confidence threshold* from the extractor LLM. Pages without citations to session traces should auto-expire after N days.

## 2. Planner Tier 2 latency will regress end-to-end performance severely

§4.7 places `PageIndex.route()` at Tier 2, called *before every LLM call* when keyword tier misses. The existing planner p50 is ~5ms (keyword) / ~5ms (embedding). PageIndex is 1–3s — **200–600× slower**.

In a 10-turn conversation with keyword misses on half the turns, that's 5–15s of planning overhead *before* any LLM generation. The §9.3 regression gate allows +10% end-to-end latency, but PageIndex alone will blow past that on any conversation where it's triggered more than once.

**Fix:** Cache PageIndex route results per session. If turn 3 asks a follow-up to turn 2's topic, reuse the prior route. Or, move PageIndex to an *explicit* memory augmentation step (like the existing trace-store injection in §7.2 of MEMORY_DESIGN.md) rather than a planner tier. The planner should route to *tools*; the memory system should inject *context*.

## 3. Schema contradictions and under-specified mechanics

| Issue | Location | Problem |
|-------|----------|---------|
| **ID format** | §3.2 shows `doc_004`; §8 Goal 1 AC says "UUID-based `id`" | Pick one. UUIDs are correct (no race condition). |
| **Trace store schema** | §7.2 says "Remove `session_embeddings` table"; §7.4 says "simply ignored" | These contradict. Removing it breaks existing DBs; ignoring it is safer. |
| **Partitioning rule** | §4.3 has `max_nodes_per_index: 100` and `token_threshold: 4000` | Which wins? If 50 nodes = 5000 tokens, what happens? |
| **Chunk sync** | §4.6 says BM25 chunks live in `wiki_chunks.db` | No strategy for keeping chunks in sync with wiki edits. Rebuild on every write? |
| **Sub-LLM model** | §11 hard-codes `sub_llm_model: "claude-haiku"` | Breaks the harness's model-agnostic provider abstraction. Should reference a model *name* from config, not a vendor-specific ID. |
| **Plan dependencies** | §5.4 example plan has implicit sequential deps | No syntax for expressing dependencies (DAG? linear list?). What if step 2 needs output from step 1 *and* step 3? |

## 4. RLMInterpreter is better but still under-specified

The declarative JSON plan is a big improvement over `eval()`. But:

- **Plan generation:** Who generates the JSON? The main LLM via a structured-output call? A separate "planner" LLM? The prompt for this is not specified.
- **Plan validation:** The example references `"page_id": "doc_004"` — does the LLM know wiki IDs? How are plan arguments validated against the wiki schema?
- **Conditional logic / loops:** `ALLOWED_TOOLS` has 4 fixed operations. No `if`, `for`, or `filter` on intermediate results. For a 500K-char document with 10 chunks, the plan in §5.4 is hand-authored-looking; a real LLM might want to loop or conditionally skip chunks. Without loop support, the plan size scales linearly with chunk count.
- **VRAM detection:** §5.5 mentions `nvidia-smi` or `ollama` API. What about Apple Silicon (no nvidia-smi)? What if the model runs via `llama.cpp` or a remote API with no local VRAM? This needs a fallback strategy.

## 5. `source_session` still dangles after 30 days

§3.2 keeps `source_session: session_uuid_abc123` with no mitigation for trace-store retention (30 days, 10K entries). After a month, the UUID is unresolvable. This undermines provenance.

**Fix:** Either remove `source_session` and replace it with an inline citation summary ("Source: session on 2026-04-10 about database scaling"), or promote pages to `verified` before their source session ages out — forcing a re-validation cycle.

## 6. Index rebuild cost is unbounded

§11 sets `rebuild_on_change: true`. For a wiki with 1000 pages, a full `pageindex.rebuild(wiki)` means:
- Reading 1000 markdown files
- Sending them to an LLM for categorization
- Rewriting `index.json` and possibly N sub-indexes

This is O(pages) and could take minutes. With concurrent sessions both writing pages and triggering rebuilds, the index is in constant flux.

**Fix:** Rebuild should be incremental (only re-index the changed page and its parent category). Full rebuild should be a manual `vibe memory wiki index rebuild` command, not automatic.

## 7. Missing: extraction prompt, quality signal, and noise floor

§6.1 step 5 says "extract key facts from messages" but never specifies:
- The prompt template for extraction
- How the extractor distinguishes signal (novel commands, file edits, decisions) from noise (chitchat, failed attempts, retry loops)
- What happens when extraction returns nothing interesting (no-op vs. writes a stub page)

Without this, the wiki will fill with low-signal pages like "User asked about Python" and "Assistant suggested checking docs."

## 8. Three SQLite databases is unnecessary sprawl

The design adds `wiki_chunks.db` (§4.6) alongside existing `traces.db` and `evals.db`. All three are local SQLite files under `~/.vibe/memory/`. There's no architectural reason these can't share a connection or at least a single database file with separate tables. Three databases means three WAL files, three connection pools, three backup/restore surfaces.

**Fix:** Use `~/.vibe/memory/memory.db` with tables `sessions`, `evals`, `wiki_chunks`, `wiki_embeddings` (optional). Simpler, atomic backups, fewer file descriptors.

## 9. Background "thread" clashes with asyncio harness

§3.5 and §8 Goal 6 AC repeatedly say "background thread" for wiki extraction. The existing harness is fully `asyncio`-based (`QueryLoop`, `LLMClient`, `ToolExecutor` all use `async/await`). Mixing `threading` and `asyncio` without an explicit executor or event loop policy is a recipe for subtle bugs (loop-in-thread, unawaited coroutines).

**Fix:** Use `asyncio.create_task()` or an `asyncio.Queue` with a worker loop, not `threading.Thread`.

## 10. Testing strategy is still weak

§8 acceptance criteria include coverage targets ("90%+ coverage for CRUD") and tiny corpuses ("20 wiki pages"). These are not meaningful quality gates.

What's missing:
- **Golden wiki test set:** A known-good wiki + index where `route()` accuracy can be measured reproducibly
- **Adversarial extraction test:** Sessions with hallucinated content that *should not* become wiki pages
- **Concurrency torture test:** 10 parallel sessions writing to the same wiki category
- **RLM accuracy benchmark:** A standardized 500K-char document with known answers, not just a one-off test
- **Planner regression test:** Prove that `tripartite_enabled=false` preserves existing behavior exactly

## Summary

v2 fixed the most dangerous parts of v1 (Python REPL, routing latency fantasy, link fragility). The remaining risks are:

1. **Auto-extraction without quality gates** → wiki becomes a garbage dump of hallucinations
2. **Planner Tier 2 at 1–3s** → will regress multi-turn conversation latency by 10–50×
3. **Under-specified mechanics** → plan generation, chunk sync, ID format, and rebuild semantics have gaps

My recommendation: **Ship Phase 1 as explicit, opt-in memory augmentation (not a planner tier).** Let the user trigger wiki writes with `vibe memory wiki create` or a confirmation prompt. Measure signal-to-noise and latency on real sessions before enabling `auto_extract`. The RLMInterpreter and BM25 pre-filter are solid; the risk is in the *curation policy*, not the architecture.

---

*End of critique*
