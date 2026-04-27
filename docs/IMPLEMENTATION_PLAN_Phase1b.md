# Phase 1b & Phase 2 Tripartite Memory System

This implementation plan covers the remaining tasks for the Tripartite Memory System integration.

## Proposed Changes

### Phase 1b — Async Extraction
Extracts factual knowledge from completed sessions without blocking user interaction.

#### [MODIFY] [query_loop.py](file:///Users/rsong/DevSpace/vibe-agent/vibe/core/query_loop.py)
- **Implement `_extract_knowledge()`**:
  - Only executes if `self.wiki` is present and `self.wiki.auto_extract` config is enabled.
  - Compiles the session transcript from `self.messages`.
  - Prompts `self.llm` to extract new factual insights (using a JSON schema: `[{"title": "...", "content": "...", "tags": [...]}]`).
  - Calls `self.wiki.create_page()` for each extracted insight with `status="draft"` and citing the current `session_id`.
- **Hook in `run()`**:
  - In the `finally:` block of `QueryLoop.run()`, if the session reached `QueryState.COMPLETED`, spawn the extraction as an asyncio task:
    `self._wiki_extract_task = asyncio.create_task(self._extract_knowledge())`
  - `close()` already properly cancels or awaits `_wiki_extract_task`.

### Phase 2 — RLM Scaling Triggers
Monitors telemetry to decide when to trigger Recursive Language Model fine-tuning.

#### [MODIFY] [telemetry.py](file:///Users/rsong/DevSpace/vibe-agent/vibe/memory/telemetry.py)
- Add `check_rlm_thresholds(db: SharedMemoryDB) -> bool`:
  - Queries the `_telemetry` table.
  - Checks if metric thresholds (e.g., > 100 compactions or > 500k tokens processed since last training event) are crossed.

#### [MODIFY] [query_loop.py](file:///Users/rsong/DevSpace/vibe-agent/vibe/core/query_loop.py)
- In the `finally:` block of `run()`, after recording session telemetry, call `check_rlm_thresholds()`.
- If triggered, log an actionable warning or launch a background `_rlm_trigger_task` to simulate scaling/fine-tuning initiation.

### Quality Gates — Contradiction Detection
Ensures new wiki pages do not contradict established knowledge.

#### [MODIFY] [wiki.py](file:///Users/rsong/DevSpace/vibe-agent/vibe/memory/wiki.py)
- In `update_page()` (and potentially `create_page()`):
  - If `self._flash_client` is available, perform a quick BM25 search against the new content to find the top 3 related existing pages.
  - Call `await self._flash_client.detect_contradiction(new_content, existing_contents)`.
  - If a contradiction is detected, force the page `status = "draft"` (even if it met the promotion criteria) and log a warning.

#### [MODIFY] [query_loop_factory.py](file:///Users/rsong/DevSpace/vibe-agent/vibe/core/query_loop_factory.py)
- Instantiate `FlashLLMClient` (using default `qwen3:1.7b` or configured flash model) and inject it into `LLMWiki` via `wiki._flash_client = flash_client` during tripartite initialization.

### CLI Polish — vibe memory status

#### [MODIFY] [main.py](file:///Users/rsong/DevSpace/vibe-agent/vibe/cli/main.py)
- Add `vibe memory status` command.
- Queries `wiki.list_pages()` to summarize Draft vs Verified page counts.
- Loads `PageIndex` to display the total number of index routing nodes.
- Queries `SharedMemoryDB` telemetry for a quick summary (e.g., total sessions, total tokens compacted).
- Renders as a formatted Rich `Panel` or `Table`.

## Verification Plan

### Automated Tests
- **`test_query_loop_extraction.py`**: Verify that a mocked LLM returns JSON insights and `QueryLoop._wiki_extract_task` correctly creates wiki draft pages.
- **`test_wiki_quality_gates.py`**: Verify that `update_page()` demotes a page to draft when `FlashLLMClient.detect_contradiction()` returns `True`.
- **`test_telemetry_rlm.py`**: Verify that threshold functions correctly parse SQLite telemetry data to trigger RLM flags.

### Manual Verification
- Run `vibe memory status` in the terminal to verify the beautiful CLI output.
- Set `memory.wiki.auto_extract = True`, run a conversation, and check `vibe memory wiki list` for new auto-extracted drafts.
