# Tripartite Memory System — Phase 1b + 2 + Quality Gates + CLI Polish
## Detailed Task List (Updated per Gemini Review)

### Phase 1b: Gated Auto-Extraction (Async Background Loop)
**Goal**: Implement `_wiki_extract_task` background loop in QueryLoop to extract knowledge from completed conversations without blocking user interaction.

**1b.1 Wiki Extraction Prompt Template**
- File: `vibe/memory/extraction.py` (NEW) — DONE
- Create `EXTRACTION_PROMPT` constant with structured prompt for LLM to extract knowledge from conversation
- Prompt requests: title, content (markdown), tags, citations (session_id, source_message_index)
- Includes instructions for [[slug]] wiki link syntax
- Gemini note: Use JSON schema `[{"title": "...", "content": "...", "tags": [...]}]` for structured extraction

**1b.2 KnowledgeExtractor class**
- File: `vibe/memory/extraction.py` (NEW) — DONE
- Class `KnowledgeExtractor` with:
  - `__init__(self, llm_client, wiki, pageindex, flash_client=None, config=None)`
  - `async def extract_from_session(self, messages, session_id) -> list[dict]`
    - Build conversation transcript from messages (skip system/tool messages)
    - Call LLM with extraction prompt
    - Parse JSON response into structured knowledge items
    - Strip markdown code fences if present
    - Return list of dicts: `{title, content, tags, citations}`
  - `async def score_novelty(self, items) -> list[float]`
    - Use PageIndex BM25 to check if similar content already exists
    - Return novelty scores (0.0 = duplicate, 1.0 = entirely new)
    - If PageIndex unavailable, return [1.0] * len(items)
  - `async def apply_gates(self, items, novelty_threshold, confidence_threshold) -> list[dict]`
    - Filter items by novelty_threshold (default from WikiConfig)
    - Score confidence via FlashLLMClient if available
    - Filter by confidence_threshold
    - Return gated (approved) items only
  - Error policy: all methods catch exceptions and return safe defaults

**1b.3 Wire _wiki_extract_task into QueryLoop.run()**
- File: `vibe/core/query_loop.py` — DONE
- In `run()` finally block, after telemetry recording:
  - If `self.wiki` is not None AND `self._config_memory.wiki.auto_extract` is True:
    - Spawn `self._wiki_extract_task = asyncio.create_task(self._extract_to_wiki(messages_copy, session_id))`
  - Copy messages to avoid mutation during extraction
  - Gemini note: Only spawn if session reached COMPLETED state

**1b.4 Implement _extract_to_wiki() method**
- File: `vibe/core/query_loop.py` — DONE
- `async def _extract_to_wiki(self, messages, session_id) -> None`
  - Create KnowledgeExtractor instance
  - Call `extract_from_session()` then `apply_gates()`
  - For each approved item:
    - Check if page with similar title exists via `_find_existing_page()`
    - If exists: call `wiki.update_page()` with merged content + citations
    - If new: call `wiki.create_page()` with status="draft"
  - Log results (created N, updated M, rejected K)
  - Catch all exceptions — extraction must NEVER crash the session

**1b.5 Config updates for auto_extract**
- File: `vibe/core/config.py` — DONE
- Added `extraction_batch_size: int = 5` (max items per extraction call)
- Added `extraction_timeout_seconds: float = 30.0`
- Gemini note: Ensure config is passed through QueryLoopFactory to QueryLoop

**1b.6 Unit tests for KnowledgeExtractor**
- File: `tests/memory/test_extraction.py` (NEW) — PENDING
- Test `extract_from_session` with mocked LLM returning valid JSON
- Test `extract_from_session` with malformed JSON (graceful handling)
- Test `extract_from_session` with markdown code fences (strip properly)
- Test `score_novelty` with mocked PageIndex
- Test `apply_gates` filtering by thresholds
- Test that extraction never raises (swallows all exceptions)

**1b.7 Unit tests for _wiki_extract_task integration**
- File: `tests/core/test_query_loop_wiki.py` (NEW or extend existing) — PENDING
- Test that auto-extraction spawns when auto_extract=True
- Test that auto-extraction does NOT spawn when auto_extract=False
- Test that auto-extraction does NOT block user response
- Test that extraction errors are caught and logged (not raised)
- Test that close() cancels pending extraction task

---

### Phase 2: RLM Scaling (Telemetry-Triggered RLM Activation)
**Goal**: Use `_telemetry` data to trigger RLM training when compaction/session metrics cross thresholds.

**2.1 RLM Threshold Analyzer**
- File: `vibe/memory/rlm_analyzer.py` (NEW) — DONE
- Class `RLMThresholdAnalyzer` with:
  - `__init__(self, telemetry, config)`
  - `async def analyze() -> RLMTriggerDecision`
  - Queries telemetry DB for:
    - % of sessions with total_chars > threshold (default 100K)
    - % of sessions with compaction events
    - Average session duration trend
  - Returns `RLMTriggerDecision`: `{should_trigger, reason, metrics}`
- Gemini note: Add `check_rlm_thresholds()` helper in telemetry.py as alternative entry point

**2.2 RLMConfig expansion**
- File: `vibe/core/config.py` — DONE
- Added: `trigger_threshold_chars`, `trigger_threshold_compaction_pct`, `trigger_window_sessions`, `min_sessions_before_trigger`, `rlm_model_path`

**2.3 Wire RLM trigger into QueryLoop**
- File: `vibe/core/query_loop.py` — DONE
- After telemetry recording in `run()` finally block:
  - If `self._telemetry` and `self._config_memory.rlm.enabled`:
    - Spawn background task `asyncio.create_task(self._maybe_trigger_rlm())`
  - `_maybe_trigger_rlm()` calls RLMThresholdAnalyzer, logs decision
  - Phase 2 MVP: only LOG the decision, do NOT actually train (training is Phase 3)

**2.4 Unit tests for RLMThresholdAnalyzer**
- File: `tests/memory/test_rlm_analyzer.py` (NEW) — PENDING
- Test trigger when compaction % exceeds threshold
- Test no-trigger when insufficient sessions
- Test no-trigger when metrics below threshold
- Test with mocked TelemetryCollector

---

### Quality Gates: Wire FlashLLMClient to Contradiction Detection
**Goal**: Use FlashLLMClient for contradiction detection when updating wiki pages.

**3.1 Wire flash_client into LLMWiki**
- File: `vibe/memory/wiki.py` — DONE
- Added `set_flash_client()` method
- Gemini note: Also consider wiring in `create_page()` for new-page contradiction detection

**3.2 Contradiction detection in update_page()**
- File: `vibe/memory/wiki.py` — DONE
- Before writing, if `_flash_client` is set and available:
  - Fetch content of pages that link TO this page (backlinks)
  - Call `flash_client.detect_contradiction(new_content, existing_contents)`
  - If contradiction: downgrade status to "draft", add contradiction flag to citations
- If flash client unavailable, proceed normally (no behavioral change)

**3.3 Wire flash_client in QueryLoopFactory**
- File: `vibe/core/query_loop_factory.py` — PENDING
- Gemini note: Instantiate FlashLLMClient using default `qwen3:1.7b` or configured flash model
- Inject into LLMWiki via `wiki.set_flash_client(flash_client)` during tripartite initialization

**3.4 Unit tests for contradiction detection**
- File: `tests/memory/test_wiki_quality_gates.py` (NEW) — PENDING
- Test update_page with contradiction detected → status drops to "draft"
- Test update_page without contradiction → status promoted to "verified" if criteria met
- Test update_page with flash client unavailable → normal behavior
- Mock FlashLLMClient to return True/False/Unavailable

---

### CLI Polish: `vibe memory status` Command
**Goal**: Add CLI command showing wiki page count, index size, telemetry summary.

**4.1 CLI command implementation**
- File: `vibe/cli/main.py` — DONE
- Function `memory_status()`:
  - Count .md files in wiki directory
  - Count verified vs draft pages
  - Read slug_index.json for index size
  - Query telemetry DB for 24h summary
  - Print formatted Rich Table

**4.2 Import fix for _parse_page_file**
- File: `vibe/cli/main.py` — DONE
- Added import from vibe.memory.wiki

**4.3 Unit tests for CLI command**
- File: `tests/cli/test_memory_commands.py` (NEW) — PENDING
- Test output formatting with mocked wiki/telemetry
- Test graceful handling when wiki not initialized

---

## Implementation Order (Updated)

**COMPLETED:**
1. 1b.1 + 1b.2: KnowledgeExtractor (foundation)
2. 1b.5: Config updates
3. 1b.3 + 1b.4: Wire into QueryLoop
4. 2.1 + 2.2: RLM config + analyzer
5. 2.3: Wire RLM into QueryLoop
6. 3.1 + 3.2: FlashLLMClient wired to wiki update_page()
7. 4.1 + 4.2: CLI memory status command

**PENDING:**
8. 3.3: Wire flash_client in QueryLoopFactory
9. 1b.6: Unit tests for KnowledgeExtractor
10. 1b.7: Unit tests for _wiki_extract_task integration
11. 3.4: Unit tests for contradiction detection
12. 2.4: Unit tests for RLMThresholdAnalyzer
13. 4.3: Unit tests for CLI command

## Quality Gates Between Phases
- After all PENDING items: Gemini CLI code review → user approval → DONE
