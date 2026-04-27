# Tripartite Memory System: Design Document for Vibe-Agent

**Date:** 2026-04-26  
**Status:** Draft — Gemini CLI Reviewed & Finalized  
**Scope:** Full implementation plan for integrating the Tripartite Memory System (LLM Wiki + PageIndex + RLM) into the existing vibe-agent memory architecture.

---

## 1. Executive Summary

The vibe-agent project currently has a solid but limited memory stack: a trace store for episodic session logging, an eval store for benchmarking, and a context compactor for working memory management. All three are valuable but share a critical limitation: they are **retrieval-oriented** rather than **reasoning-oriented**. The trace store finds "similar" sessions via vector similarity. The compactor blindly truncates or summarizes. Neither *understands* the knowledge they hold.

The **Tripartite Memory System** introduces a **reasoning-first memory framework** that mimics how humans use a textbook. While it minimizes reliance on vector similarity for core routing, it pragmatically incorporates embeddings as a scalability tier rather than dogmatically excluding them:

1. **The Index (PageIndex)** — A lightweight JSON "Table of Contents" that the LLM reads and reasons over to decide where knowledge lives.
2. **The Storage (LLM Wiki)** — Persistent, interlinked Markdown files written and maintained by the agent itself. Knowledge compounds over time.
3. **The Execution (RLM)** — A Python REPL environment where the agent programmatically decomposes large documents, delegates reading to sub-LLMs, and synthesizes answers without hitting context limits.

This document specifies the integration goals, implementation phases, API contracts, and acceptance criteria for bringing this system into vibe-agent.

---

## 2. Current State vs. Target State

### 2.1 Current Memory Architecture (As-Is)

| Component | Purpose | Persistence | Reasoning? |
|-----------|---------|-------------|------------|
| `SQLiteTraceStore` | Episodic memory (session logs) | `~/.vibe/memory/traces.db` | No — vector similarity only |
| `EvalStore` | Benchmark results | `~/.vibe/memory/evals.db` | No — structured data |
| `ContextCompactor` | Working memory (token budget) | In-memory only | No — truncation/summarization |
| `HybridPlanner` | Tool/skill selection | In-memory cache | Partial — keyword/embedding tiers |
| `WikiMemory` (archived) | Cross-session knowledge | `~/.vibe/wiki/*.md` | No — flat files, no index |

**Key Gaps:**
- Trace store vector search is brute-force O(N), loads all embeddings into memory.
- No active knowledge curation — the agent never *writes* what it learned into persistent, structured notes.
- Context compactor loses information permanently (TRUNCATE/DROP) rather than archiving it.
- No mechanism for the agent to read documents larger than its context window.

### 2.2 Target Architecture (To-Be)

| Layer | Component | Role | Replaces / Augments |
|-------|-----------|------|---------------------|
| **Index** | `PageIndex` | JSON tree of wiki topics; LLM reasons over it to route queries | Replaces brute-force vector search in trace store |
| **Storage** | `LLMWiki` | Markdown files with YAML frontmatter, wiki-links, auto-curated by agent | Revives archived `WikiMemory` with active curation |
| **Execution** | `RLMExecutor` | Python REPL with `load_wiki_file()` and `llm_query_async()` for chunking | Augments context compactor for large-document handling |
| **Integration** | `TripartiteMemoryManager` | Orchestrates Index → Storage → Execution flow; wires into `QueryLoop` | New coordinator alongside existing ones |

---

## 3. Design Principles

1. **Reasoning-led, Vector-Augmented.** Reasoning over structured indices is the primary routing mechanism. Vectors/embeddings are used as a fallback or pre-filter for extremely large indices, rather than the core retrieval logic.
2. **Agent-authored knowledge.** The LLM Wiki is written by the agent, not ingested from external documents. Every page is a *synthesis* of what the agent learned.
3. **Recursive decomposition.** Large documents are never loaded whole into the main LLM context. The RLM layer breaks them into chunks and delegates to sub-LLMs.
4. **Backward compatible.** Existing trace store, eval store, and context compactor remain functional. The Tripartite system is additive.
5. **Graceful degradation.** If the RLM layer fails (infinite loop, bad code), the system falls back to standard context compaction.
6. **Fail-Safe Integrity.** Use atomic writes (temp-file + rename) for all persistent metadata and knowledge files.
7. **Zero-Trust REPL.** The RLM executor must be sandboxed via **RestrictedPython** for language-level safety and OS-level subprocess isolation (e.g., `sandbox-exec`).

---

## 4. Component Specifications

### 4.1 Layer 1: LLM Wiki (Storage)

**Concept:** Andrej Karpathy's LLM Wiki pattern. The agent incrementally builds and maintains a persistent, interlinked collection of Markdown files.

#### 4.1.1 File Schema

Every wiki page is a `.md` file with YAML frontmatter:

```yaml
---
id: doc_004
title: Infrastructure Logs
date_created: 2026-04-10
last_updated: 2026-04-26
status: draft | verified
tags: [database, scaling, servers]
source_session: "uuid-of-session-that-created-this"
---

# Infrastructure Logs

Content goes here. Wiki-links connect to other pages:

See also [[Database_Scaling]] and [[Outage_Response_Playbook]].
```

**Constraints:**
- `id` must be unique across the wiki. Format: `doc_{seq}` or semantic slug.
- `status` tracks knowledge maturity. `draft` is agent-curated; `verified` is user-approved.
- `title` is human-readable and appears in the PageIndex.
- `source_session` links back to the trace store session that originated this knowledge.
- **Redaction:** Content MUST be passed through `SecretRedactor` before being written to disk.

#### 4.1.2 Storage Layout

```
~/.vibe/
├── memory/
│   ├── traces.db          # existing
│   └── evals.db           # existing
├── wiki/                  # NEW: LLM Wiki root
│   ├── index.json         # PageIndex (Layer 2)
│   ├── infrastructure_logs.md
│   ├── database_scaling.md
│   ├── outage_response_playbook.md
│   └── ...
└── config.yaml
```

#### 4.1.3 Wiki Page Lifecycle

```
1. Agent learns something new during a session
   └── e.g., "We fixed a database scaling issue by adding read replicas"

2. TripartiteMemoryManager decides: create new page vs. update existing
   └── Query LLM: "Does wiki page [[Database_Scaling]] already cover read replicas?"
   └── If yes: append/update. If no: create new page.

3. Agent writes/updates the Markdown file with proper frontmatter
   └── Redact secrets -> Atomic write to temp file -> Rename to target .md

4. PageIndex is updated to reflect new/updated page

5. On future queries, PageIndex routes to this page
```

#### 4.1.4 API Surface

```python
class LLMWiki:
    def __init__(self, wiki_dir: Path = DEFAULT_WIKI_DIR, 
                 redactor: SecretRedactor | None = None): ...
    
    def create_page(self, title: str, content: str, tags: list[str], 
                    source_session: str | None = None,
                    status: str = "draft") -> WikiPage: ...
    
    def update_page(self, page_id: str, new_content: str | None = None,
                    append_content: str | None = None,
                    new_tags: list[str] | None = None) -> WikiPage: ...
    
    def get_page(self, page_id: str) -> WikiPage | None: ...
    
    def list_pages(self, tag: str | None = None) -> list[WikiPageSummary]: ...
    
    def resolve_link(self, wiki_link: str) -> WikiPage | None: ...
```

---

### 4.2 Layer 2: PageIndex (Index)

**Concept:** Hierarchical, reasoning-based routing. A JSON tree that the LLM traverses iteratively to locate knowledge.

#### 4.2.1 Schema

```json
{
  "wiki_index": {
    "node_id": "root_01",
    "title": "Master Knowledge Base",
    "description": "Top-level index of all agent knowledge.",
    "sub_nodes": [
      {
        "node_id": "category_infra",
        "title": "Infrastructure",
        "description": "Server logs, database scaling, and DevOps playbooks.",
        "sub_nodes": [
          {
            "node_id": "doc_004",
            "title": "Infrastructure Logs",
            "description": "Historical data on server performance...",
            "file_path": "infrastructure_logs.md",
            "last_updated": "2026-04-26",
            "status": "draft"
          }
        ]
      }
    ]
  }
}
```

#### 4.2.2 Hierarchical Routing Algorithm

To avoid loading the entire tree into context, the agent performs an **Iterative Drill-down**:

```python
async def route_query(self, query: str) -> list[RankedNode]:
    """
    1. Load Top-Level nodes into context.
    2. Ask LLM: "Which top-level categories are relevant?"
    3. For each relevant category, load its immediate children.
    4. Repeat until leaf nodes (documents) are reached.
    5. Return ranked leaf nodes.
    """
```

**Constraints:**
- Tree depth is capped at 4 levels.
- **Atomic Writes:** Use temp-file + rename pattern.

---

### 4.3 Layer 3: RLM Executor (Execution)

**Concept:** Recursive Language Models in a highly restricted Python environment.

#### 4.3.1 Environment & Sandboxing

The RLM executor provides a **tri-layer sandbox**:
1.  **Language Sandbox:** **RestrictedPython** to prevent access to `__builtins__`, `__import__`, etc.
2.  **Static Analysis:** AST-based verification.
3.  **OS Sandbox:** Subprocess execution via `sandbox-exec` (macOS) to enforce `deny file-write*` and `deny network*`.

#### 4.3.2 API Surface

```python
class RLMExecutor:
    def __init__(self, 
                 wiki: LLMWiki,
                 llm_client: LLMClient,
                 flash_model: str | None = None,
                 max_tokens_budget: int = 500000): ...
```

---

### 4.4 Integration Layer: TripartiteMemoryManager

#### 4.4.1 Integration Flow (Background Curation)

Curation is decoupled from the main loop to prevent blocking:

```
User Query -> augment_query() -> Route -> RLM -> Prompt
Session End -> Enqueue messages to CurationQueue
Background Worker -> Process Queue -> update wiki/index
```

#### 4.4.2 Integration Hook: RLM_ARCHIVE

`ContextCompactor` now supports `Strategy: RLM_ARCHIVE`. Instead of dropping messages, RLM summarizes them into a new wiki page in the background.

#### 4.4.3 Wiring into QueryLoop

```python
# In QueryLoop.run() finally block:

# 1. Log session to TraceStore
await self.trace_store.log_session(session_id, ...)

# 2. ASYNC: Dispatch curation to background thread/worker
self.memory_manager.enqueue_curation(session_id, self.messages)
```

---

## 5. Implementation Phases

### Phase 1: Foundation (LLM Wiki + PageIndex)
- Implement `LLMWiki` with `SecretRedactor`.
- Implement Hierarchical `PageIndex` with iterative routing.

### Phase 2: RLM Executor
- Implement `RLMExecutor` with **RestrictedPython** and OS sandboxing.
- Implement token-based budget enforcement.

### Phase 3: Integration & Orchestration
- Implement `TripartiteMemoryManager` with background `CurationQueue`.
- Implement **RLM_ARCHIVE** compaction strategy.

---

## 6. Configuration

```yaml
tripartite_memory:
  enabled: true
  rlm:
    flash_model: "qwen3-coder"
    max_tokens_budget: 500000        # Total tokens per RLM execution
    timeout_seconds: 120
  curation:
    background_processing: true      # Run curation in separate thread
    queue_size: 100
```

---

## 7. Acceptance Criteria (Cross-Phase)

### 7.3 Safety

| # | Criterion | Verification |
|---|-----------|--------------|
| S1 | RestrictedPython enforcement | Attempt `__import__('os')`, verify `PermissionError` |
| S2 | **Secrets redacted in wiki pages (Phase 1)** | Store API key in session, verify redaction |
| S3 | **RLM process cannot write to disk (Phase 2)** | Attempt write in RLM, verify sandbox failure |
| S4 | **Hierarchical routing latency** | Verify routing < 500ms for 1000-node index |

---

*End of Finalized Design Document*
