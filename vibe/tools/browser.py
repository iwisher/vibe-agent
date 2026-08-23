"""Adaptive Dual-Tier Browser Tool for Vibe Agent.

Provides fast static HTML & document reading (Tier 1, default) alongside
optional dynamic headless browser automation via Playwright (Tier 2).
Includes SSRF validation and response payload protection.
"""

from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import logging
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from vibe.tools.tool_system import Tool, ToolResult

logger = logging.getLogger(__name__)

# Check optional Docling availability
try:
    from docling.document_converter import DocumentConverter

    HAS_DOCLING = True
except ImportError:
    DocumentConverter = None  # type: ignore[misc,assignment]
    HAS_DOCLING = False

# Check optional Playwright availability
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


# ===========================================================================
# SSRF Protection Guard
# ===========================================================================


class SSRFGuard:
    """Validates URLs to prevent SSRF against loopback, private, and metadata IPs."""

    FORBIDDEN_NETWORKS = [
        ipaddress.ip_network("127.0.0.0/8"),  # IPv4 Loopback
        ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918 Private
        ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918 Private
        ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918 Private
        ipaddress.ip_network("169.254.0.0/16"),  # Link-Local & Cloud Metadata (169.254.169.254)
        ipaddress.ip_network("0.0.0.0/8"),  # Local identification
        ipaddress.ip_network("::1/128"),  # IPv6 Loopback
        ipaddress.ip_network("fc00::/7"),  # IPv6 Unique Local
        ipaddress.ip_network("fe80::/10"),  # IPv6 Link-Local
    ]

    @classmethod
    def is_safe(cls, url: str) -> bool:
        """Return True if URL is safe to fetch, False if blocked by SSRF policy."""
        try:
            parsed = urlparse(url)
            if parsed.scheme.lower() not in ("http", "https"):
                return False

            hostname = parsed.hostname
            if not hostname:
                return False

            # Check well-known forbidden hostnames
            if hostname.lower() in ("localhost", "0.0.0.0"):
                return False

            # Check if host is direct IP address
            try:
                ip = ipaddress.ip_address(hostname)
                return not any(ip in net for net in cls.FORBIDDEN_NETWORKS)
            except ValueError:
                pass  # Hostname is a domain name, resolve via DNS

            # Resolve DNS
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addr_info = socket.getaddrinfo(hostname, port)
            for _, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                if any(ip in net for net in cls.FORBIDDEN_NETWORKS):
                    return False

            return True
        except Exception as e:
            logger.debug(f"SSRF validation error for {url}: {e}")
            return False


def is_safe_url(url: str) -> bool:
    """Convenience helper for SSRF validation."""
    return SSRFGuard.is_safe(url)


# ===========================================================================
# HTML to Markdown Parser
# ===========================================================================


class _MarkdownHTMLParser(HTMLParser):
    """Simple, fast HTML-to-markdown parser using Python stdlib."""

    IGNORED_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "svg", "iframe"}

    def __init__(self) -> None:
        super().__init__()
        self._output: list[str] = []
        self._ignore_depth = 0
        self._current_href: str | None = None
        self._link_text: list[str] = []
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.IGNORED_TAGS:
            self._ignore_depth += 1
            return

        if self._ignore_depth > 0:
            return

        attr_dict = dict(attrs)

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_lower[1])
            self._output.append(f"\n\n{'#' * level} ")
        elif tag_lower in ("p", "div", "section", "article"):
            self._output.append("\n\n")
        elif tag_lower == "br":
            self._output.append("\n")
        elif tag_lower == "li":
            self._output.append("\n- ")
        elif tag_lower == "pre":
            self._in_pre = True
            self._output.append("\n\n```\n")
        elif tag_lower == "code" and not self._in_pre:
            self._output.append("`")
        elif tag_lower == "a":
            self._current_href = attr_dict.get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.IGNORED_TAGS:
            if self._ignore_depth > 0:
                self._ignore_depth -= 1
            return

        if self._ignore_depth > 0:
            return

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "div"):
            self._output.append("\n")
        elif tag_lower == "pre":
            self._in_pre = False
            self._output.append("\n```\n")
        elif tag_lower == "code" and not self._in_pre:
            self._output.append("`")
        elif tag_lower == "a":
            link_content = "".join(self._link_text).strip()
            if self._current_href and link_content:
                self._output.append(f"[{link_content}]({self._current_href})")
            elif link_content:
                self._output.append(link_content)
            self._current_href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._ignore_depth > 0:
            return
        if self._current_href is not None:
            self._link_text.append(data)
        else:
            self._output.append(data)

    def get_markdown(self) -> str:
        text = "".join(self._output)
        # Collapse multiple blank lines
        lines = [line.rstrip() for line in text.split("\n")]
        cleaned: list[str] = []
        consecutive_empty = 0
        for line in lines:
            if not line:
                consecutive_empty += 1
                if consecutive_empty <= 2:
                    cleaned.append("")
            else:
                consecutive_empty = 0
                cleaned.append(line)
        return "\n".join(cleaned).strip()


def html_to_markdown(html_text: str) -> str:
    """Convert HTML markup into clean markdown text."""
    parser = _MarkdownHTMLParser()
    parser.feed(html_text)
    return parser.get_markdown()


class StaticHtmlExtractor:
    """Extracts markdown from HTML with optional Docling acceleration."""

    def extract(self, html_text: str, max_chars: int = 20000) -> str:
        md = html_to_markdown(html_text)
        if len(md) > max_chars:
            return md[:max_chars] + "\n\n... [truncated...]"
        return md


# ===========================================================================
# Playwright Dynamic Runner (Tier 2)
# ===========================================================================


async def _run_playwright(url: str, action: str = "read", selector: str | None = None) -> str:
    """Execute dynamic browser navigation and actions via Playwright."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            if action == "click" and selector:
                await page.wait_for_selector(selector, timeout=5000)
                await page.click(selector)
                await page.wait_for_timeout(1000)

            content = await page.content()
            return content
        finally:
            await browser.close()


# ===========================================================================
# BrowserTool Implementation
# ===========================================================================


class BrowserTool(Tool):
    """Adaptive Dual-Tier Browser Tool supporting fast static reads and dynamic actions."""

    def __init__(self) -> None:
        super().__init__(
            name="browse",
            description=(
                "Fetch, inspect, or interact with web pages and online documents "
                "(HTML, Markdown) using fast static parsing or dynamic headless browser rendering "
                "with SSRF protection."
            ),
        )
        self._extractor = StaticHtmlExtractor()

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The HTTP or HTTPS URL of the web page or document.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "static", "dynamic"],
                    "description": (
                        "Execution mode: 'auto' (default: static unless interaction needed), "
                        "'static' (fast HTTP fetch), or 'dynamic' (headless Playwright browser)."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["read", "click"],
                    "description": (
                        "Action to perform: 'read' (default: extract markdown content) "
                        "or 'click' (click element, then extract)."
                    ),
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector to click when action='click'.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max character length of returned markdown (default: 20000).",
                },
            },
            "required": ["url"],
        }

    async def execute(
        self,
        url: str,
        mode: str = "auto",
        action: str = "read",
        selector: str | None = None,
        max_chars: int = 20000,
        **kwargs: Any,
    ) -> ToolResult:
        start_time = time.time()

        # Step 1: Validate SSRF
        if not is_safe_url(url):
            return ToolResult(
                success=False,
                content=None,
                error=(
                    f"Blocked by safety policy (SSRF): URL '{url}' resolves to a "
                    "local/private network or disallowed scheme."
                ),
            )

        # Step 2: Determine Tier
        use_dynamic = mode == "dynamic" or (mode == "auto" and action == "click")

        # Step 3: Dynamic Execution (Tier 2)
        if use_dynamic:
            if not HAS_PLAYWRIGHT:
                if action == "click":
                    return ToolResult(
                        success=False,
                        content=None,
                        error=(
                            "Playwright is required for interactive dynamic actions: "
                            "run 'pip install playwright && playwright install chromium'."
                        ),
                    )
                logger.info("Playwright not installed; falling back to Tier 1 fast static reader.")
            else:
                try:
                    raw_html = await _run_playwright(url, action=action, selector=selector)
                    markdown_content = self._extractor.extract(raw_html, max_chars=max_chars)
                    duration = round(time.time() - start_time, 3)
                    return ToolResult(
                        success=True,
                        content=markdown_content,
                        metadata={
                            "tier": "dynamic",
                            "url": url,
                            "chars": len(markdown_content),
                            "duration_s": duration,
                        },
                    )
                except Exception as e:
                    logger.warning(
                        f"Dynamic Playwright execution failed: {e}; falling back to static."
                    )
                    if action == "click":
                        return ToolResult(
                            success=False,
                            content=None,
                            error=f"Playwright interaction error: {e}",
                        )

        # Step 4: Static Execution (Tier 1)
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = await client.get(url)
                if response.status_code >= 400:
                    phrase = response.reason_phrase or "Request failed"
                    return ToolResult(
                        success=False,
                        content=None,
                        error=f"HTTP {response.status_code}: {phrase}",
                    )

                # Try Docling if available on fetched content stream
                markdown_content: str = ""
                parser_used = "stdlib"
                if HAS_DOCLING and DocumentConverter is not None:
                    try:
                        import io

                        from docling.datamodel.base_models import DocumentStream

                        content_io = io.BytesIO(response.content)
                        stream = DocumentStream(name="page.html", stream=content_io)
                        converter = DocumentConverter()
                        conv_res = await asyncio.to_thread(converter.convert, stream)
                        markdown_content = conv_res.document.export_to_markdown()
                        parser_used = "docling"
                    except Exception as e:
                        logger.debug(f"Docling stream conversion failed: {e}; falling back.")
                        markdown_content = ""

                if not markdown_content:
                    markdown_content = self._extractor.extract(response.text, max_chars=max_chars)

                if len(markdown_content) > max_chars:
                    markdown_content = markdown_content[:max_chars] + "\n\n... [truncated...]"

                duration = round(time.time() - start_time, 3)
                return ToolResult(
                    success=True,
                    content=markdown_content,
                    metadata={
                        "tier": "static",
                        "parser": parser_used,
                        "url": url,
                        "chars": len(markdown_content),
                        "duration_s": duration,
                    },
                )
        except Exception as e:
            return ToolResult(success=False, content=None, error=f"Failed to fetch '{url}': {e}")
