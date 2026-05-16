# Multi-Agent Swarm Orchestration (v0.3.5) - Implementation Review

## 1. Orchestrator Queue Corruption (CRITICAL)
**Finding:** `SwarmOrchestrator._run_node()` registers a single shared "orchestrator" queue and all concurrent tasks compete for messages on it. A RESULT message for task A can be consumed by the waiter for task B, causing task B to timeout while task A's result is lost.

**Fix:** Use `asyncio.Future` per correlation_id. The orchestrator should create a `Future` before sending a TASK, and the message bus should resolve the Future when a matching RESULT arrives.

## 2. Broadcast Duplication
**Finding:** `EventBroker.publish()` delivers to all matching topics without deduplication. An agent subscribed to both "all" and "agent:id" receives two copies of the same broadcast.

**Fix:** Track delivered queues per publish cycle using a `set()` to ensure each queue gets at most one copy.

## 3. Wiki Update Deadlock
**Finding:** `process_wiki_updates()` is defined but never started as a background task. All UPDATE_WIKI messages are silently ignored.

**Fix:** Start the wiki update processor as a background task in `SwarmOrchestrator.run()` and cancel it on completion.

## 4. Agent Lifecycle Race
**Finding:** `SubAgent.start()` creates the task but doesn't wait for the `_run_loop` to actually start listening. Messages sent immediately after `start()` may be lost.

**Fix:** Add an `asyncio.Event` that signals when the message loop is ready, and await it in `start()`.

## 5. Missing SharedWiki Tests
**Finding:** No tests for `SharedWiki` — no coverage for read operations or update request handling.

**Fix:** Add `tests/swarm/test_shared_wiki.py` with tests for get_page, search, list_pages, get_graph, and request_update.

## 6. Missing Parallel DAG Tests
**Finding:** All DAG tests use sequential dependencies (research → code → critique). No test verifies parallel execution of independent nodes.

**Fix:** Add a test with two independent research tasks that run in parallel, verifying both complete and results are correctly attributed.

## 7. Message Bus Poison Pill
**Finding:** No test for malformed messages or agent crashes injected into the bus.

**Fix:** Add poison pill tests: send a message with unknown msg_type, simulate agent task exception, verify orchestrator handles gracefully.

## Recommendations Summary
1. Refactor orchestrator to use `asyncio.Future` for result delivery
2. Deduplicate broadcast delivery in EventBroker
3. Start wiki update background task in orchestrator
4. Add ready-event to SubAgent.start()
5. Add SharedWiki tests
6. Add parallel DAG execution tests
7. Add poison pill fault tolerance tests
