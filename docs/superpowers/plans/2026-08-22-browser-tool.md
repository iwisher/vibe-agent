# Adaptive Dual-Tier Browser Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the adaptive dual-tier `BrowserTool` (`browse` / `fetch_url`) in `vibe/tools/browser.py`, wire it to `QueryLoopFactory`, and verify with thorough unit and integration tests.

**Architecture:** A dual-tier browser tool extending `vibe.tools.tool_system.Tool` with SSRF protection. Tier 1 provides fast async HTTP + Docling / HTML markdown extraction. Tier 2 provides optional headless Playwright browser interaction with graceful fallbacks.

**Tech Stack:** Python 3.11+, `httpx`, `docling` (optional/bridge), `playwright` (optional/dynamic), `pytest`, `pytest-asyncio`.

---

### Task 1: SSRF Guard & URL Validator

**Files:**
- Create: `vibe/tools/browser.py`
- Test: `tests/tools/test_browser.py`

- [ ] **Step 1: Write failing tests for SSRF validation**
- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement `is_safe_url` and SSRF validation logic**
- [ ] **Step 4: Run tests to verify pass**

---

### Task 2: Static HTML/Docling Extraction Engine (Tier 1)

**Files:**
- Modify: `vibe/tools/browser.py`
- Test: `tests/tools/test_browser.py`

- [ ] **Step 1: Write failing tests for HTML-to-markdown and Docling extraction**
- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement `StaticHtmlExtractor` and `_fetch_static`**
- [ ] **Step 4: Run tests to verify pass**

---

### Task 3: Dynamic Playwright Engine & Adaptive Dispatch (Tier 2)

**Files:**
- Modify: `vibe/tools/browser.py`
- Test: `tests/tools/test_browser.py`

- [ ] **Step 1: Write failing tests for Playwright dynamic driver and graceful fallback**
- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement `BrowserTool` with `mode`, `action`, `selector`, `max_chars`**
- [ ] **Step 4: Run tests to verify pass**

---

### Task 4: QueryLoopFactory Wiring & End-to-End Integration

**Files:**
- Modify: `vibe/core/query_loop_factory.py`
- Test: `tests/tools/test_browser.py`

- [ ] **Step 1: Write integration tests verifying `BrowserTool` in `QueryLoopFactory`**
- [ ] **Step 2: Wire `BrowserTool` in `vibe/core/query_loop_factory.py`**
- [ ] **Step 3: Run full test suite and verify 100% pass**
