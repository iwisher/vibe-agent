# Adaptive Dual-Tier Browser Tool Specification

> **Date**: 2026-08-22  
> **Status**: Approved  
> **Target Module**: `vibe/tools/browser.py`  
> **Interface**: `Tool` in `vibe/tools/tool_system.py`

---

## 1. Overview & Objective

Provide a robust, secure, and adaptive browser tool for Vibe Agent (`browse` / `fetch_url`) that operates in two tiers:
1. **Tier 1 (Fast Static Engine — Default)**: High-speed, lightweight HTTP retrieval with Docling structured markdown extraction (and resilient standard-library HTML parser fallback). Zero browser binaries required.
2. **Tier 2 (Dynamic Playwright Driver — Optional)**: Headless browser automation when JavaScript rendering, element interaction (`click`), or dynamic single-page app (SPA) hydration is needed and `playwright` is installed.

---

## 2. Architecture & Components

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   BrowserTool (vibe/tools/browser.py)                    │
│                   Tool Name: "browse" (alias "fetch_url")                │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
┌─────────────────────────────────┐     ┌──────────────────────────────────┐
│   Tier 1: Static Engine         │     │   Tier 2: Dynamic Engine         │
│   - Async HTTP client (`httpx`) │     │   - Playwright Async API         │
│   - Docling / HTML parser       │     │   - Headless Chromium            │
│   - SSRF Protection Guard       │     │   - DOM / Click / JS execution   │
│   - Fast, zero extra binaries   │     │   - Graceful fallback & hints    │
└─────────────────────────────────┘     └──────────────────────────────────┘
```

### Key Modules:
* **`vibe/tools/browser.py`**:
  * `BrowserTool`: Core `Tool` class implementing `get_schema()` and `execute()`.
  * `SSRFGuard`: Hostname resolution & private/loopback IP validation.
  * `StaticHtmlExtractor`: Fast HTML-to-markdown converter (Docling bridge with stdlib fallback).
  * `PlaywrightDriver`: Lazy loader and runner for Playwright sessions.
* **`vibe/core/query_loop_factory.py`**:
  * Registers `BrowserTool` in default `ToolSystem`.
* **`tests/tools/test_browser.py`**:
  * Unit and integration tests covering SSRF blocking, static HTML extraction, truncation, Docling fallback, and Playwright mock execution.

---

## 3. Tool Function Schema

* **Name**: `browse`
* **Description**: `"Fetch, inspect, or interact with web pages and online documents (HTML, PDF, Markdown) using fast static parsing or dynamic headless browser rendering with SSRF protection."`

```json
{
  "name": "browse",
  "description": "Fetch, inspect, or interact with web pages and online documents using fast static parsing or dynamic browser rendering.",
  "parameters": {
    "type": "object",
    "properties": {
      "url": {
        "type": "string",
        "description": "The HTTP or HTTPS URL of the web page or document."
      },
      "mode": {
        "type": "string",
        "enum": ["auto", "static", "dynamic"],
        "description": "Execution mode: 'auto' (default, uses static unless interactive action requested), 'static' (fast HTTP read), or 'dynamic' (headless Playwright browser)."
      },
      "action": {
        "type": "string",
        "enum": ["read", "click"],
        "description": "Action to perform: 'read' (extract markdown content) or 'click' (click an element matching selector, then extract content)."
      },
      "selector": {
        "type": "string",
        "description": "CSS selector to click when action='click' in dynamic mode."
      },
      "max_chars": {
        "type": "integer",
        "description": "Maximum character count of extracted markdown content (default: 20000)."
      }
    },
    "required": ["url"]
  }
}
```

---

## 4. Security & Safety Controls

1. **SSRF Guard (`_validate_url`)**:
   - Scheme verification: Rejects anything other than `http://` and `https://`.
   - IP / Hostname verification: Resolves domain to IP via `socket.getaddrinfo`.
   - Denies private, loopback, link-local, and cloud metadata addresses:
     - `127.0.0.0/8` (Loopback / localhost)
     - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC 1918 Private)
     - `169.254.0.0/16` (Link-Local & Cloud Metadata `169.254.169.254`)
     - `::1`, `fc00::/7`, `fe80::/10` (IPv6 loopback/private/link-local)
     - `0.0.0.0`
2. **Context & Resource Guard**:
   - HTTP response body capped to 2MB raw download size.
   - Timeout default: 15.0 seconds.
   - Text output truncated to `max_chars` (default 20,000 chars) with explicit truncation annotation.

---

## 5. Execution Pipeline Details

### 5.1 Mode Resolution
- If `mode == "auto"`:
  - If `action == "click"` or explicit dynamic selector is passed: route to **Tier 2 (Dynamic)**.
  - Else: route to **Tier 1 (Static)**.
- If `mode == "static"`:
  - Execute Tier 1.
- If `mode == "dynamic"`:
  - Execute Tier 2.

### 5.2 Tier 1: Static Engine
1. Execute async HTTP GET request via `httpx.AsyncClient` with user-agent header.
2. If `docling` is available: parse document through `docling.document_converter.DocumentConverter` in `asyncio.to_thread`.
3. If `docling` is unavailable or fails: parse HTML via stdlib `html.parser.HTMLParser`, stripping `<script>`, `<style>`, `<noscript>`, `<nav>`, and `<header>` tags, converting `<h1>`-`<h6>`, `<a>`, `<p>`, `<ul>`/`<ol>` to clean markdown.

### 5.3 Tier 2: Dynamic Engine
1. Check `playwright` availability via `importlib.util.find_spec("playwright")`.
2. If `playwright` is not installed:
   - If `action == "read"`: log warning and fallback to Tier 1.
   - If `action == "click"`: return `ToolResult(success=False, error="Playwright is required for interactive dynamic actions. Install via: pip install playwright && playwright install chromium")`.
3. If `playwright` is installed:
   - Launch async Chromium browser instance in headless mode.
   - Create new isolated context with default viewport and user agent.
   - Navigate to URL (`page.goto(url, wait_until="domcontentloaded", timeout=15000)`).
   - If `action == "click"` and `selector`: wait for selector and click (`page.click(selector, timeout=5000)`).
   - Extract `page.content()`, pass to markdown extractor, and close context/browser.

---

## 6. Testing Strategy

1. **Unit Tests (`tests/tools/test_browser.py`)**:
   - `test_ssrf_blocks_localhost_and_private_ips`: Verify SSRF denial for `127.0.0.1`, `localhost`, `10.0.0.1`, `192.168.1.1`, `169.254.169.254`.
   - `test_ssrf_allows_public_urls`: Verify valid public URLs are allowed through validation.
   - `test_static_fetch_html_to_markdown`: Mock `httpx` and verify clean markdown conversion of headings, links, paragraphs, and lists.
   - `test_output_truncation`: Verify `max_chars` truncates content and appends `[truncated...]`.
   - `test_docling_converter_integration`: Verify Docling bridge when docling is present.
   - `test_playwright_dynamic_fallback_when_uninstalled`: Verify graceful hint/fallback when playwright is missing.
   - `test_playwright_dynamic_execution_when_installed`: Mock `playwright.async_api` and verify dynamic interaction.
2. **Factory Integration Test**:
   - Verify `BrowserTool` is registered and discoverable in `QueryLoopFactory.create().tools`.
