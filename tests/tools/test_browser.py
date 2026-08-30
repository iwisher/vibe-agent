"""Unit and integration tests for vibe.tools.browser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.tools.browser import (
    BrowserTool,
    StaticHtmlExtractor,
    html_to_markdown,
    is_safe_url,
)

# ===========================================================================
# SSRF Guard Tests
# ===========================================================================


def test_ssrf_guard_blocks_non_http_schemes():
    assert not is_safe_url("file:///etc/passwd")
    assert not is_safe_url("ftp://ftp.example.com")
    assert not is_safe_url("javascript:alert(1)")
    assert not is_safe_url("data:text/html,<h1>hi</h1>")


def test_ssrf_guard_blocks_private_and_loopback_ips():
    assert not is_safe_url("http://127.0.0.1")
    assert not is_safe_url("http://127.0.0.1:8080/secret")
    assert not is_safe_url("http://localhost")
    assert not is_safe_url("http://localhost:3000")
    assert not is_safe_url("http://10.0.0.1/admin")
    assert not is_safe_url("http://172.16.0.1")
    assert not is_safe_url("http://192.168.1.1")
    assert not is_safe_url("http://169.254.169.254/latest/meta-data")
    assert not is_safe_url("http://0.0.0.0")


def test_ssrf_guard_allows_valid_public_urls():
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        # Mock public IP 93.184.216.34 for example.com
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
        assert is_safe_url("https://example.com/docs")
        assert is_safe_url("http://api.github.com/repos")


# ===========================================================================
# Static HTML to Markdown Converter Tests
# ===========================================================================


def test_html_to_markdown_basic_formatting():
    html = """
    <html>
        <head><title>Test Page</title></head>
        <body>
            <header><nav><a href="/home">Home</a></nav></header>
            <h1>Welcome to Vibe</h1>
            <p>This is a <strong>bold</strong> paragraph with a <a href="https://example.com">link</a>.</p>
            <h2>Features</h2>
            <ul>
                <li>Fast</li>
                <li>Secure</li>
            </ul>
            <script>alert("evil");</script>
            <style>body { color: red; }</style>
        </body>
    </html>
    """
    md = html_to_markdown(html)
    assert "# Welcome to Vibe" in md
    assert "## Features" in md
    assert "bold" in md
    assert "[link](https://example.com)" in md
    assert "- Fast" in md
    assert "- Secure" in md
    assert "alert" not in md
    assert "color: red" not in md


def test_static_html_extractor_truncation():
    extractor = StaticHtmlExtractor()
    long_html = "<html><body><p>" + ("A" * 1000) + "</p></body></html>"
    result = extractor.extract(long_html, max_chars=100)
    assert len(result) <= 150
    assert "[truncated...]" in result


# ===========================================================================
# BrowserTool Execution Tests (Tier 1: Static)
# ===========================================================================


@pytest.mark.asyncio
async def test_browser_tool_schema():
    tool = BrowserTool()
    schema = tool.get_schema()
    assert schema["type"] == "object"
    assert "url" in schema["properties"]
    assert "url" in schema["required"]
    assert "mode" in schema["properties"]
    assert "action" in schema["properties"]


@pytest.mark.asyncio
async def test_browser_tool_blocks_ssrf():
    tool = BrowserTool()
    result = await tool.execute(url="http://127.0.0.1:8080/internal")
    assert not result.success
    assert "Blocked by safety policy (SSRF)" in result.error


@pytest.mark.asyncio
async def test_browser_tool_static_fetch_success():
    tool = BrowserTool()
    fake_html = "<html><body><h1>Sample Documentation</h1><p>Hello world.</p></body></html>"

    with patch("vibe.tools.browser.is_safe_url", return_value=True):
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = fake_html
            mock_response.content = fake_html.encode("utf-8")
            mock_response.headers = {"content-type": "text/html"}
            mock_get.return_value = mock_response

            result = await tool.execute(url="https://example.com/sample")
            assert result.success
            assert "Sample Documentation" in result.content
            assert "Hello world." in result.content
            assert result.metadata.get("tier") == "static"


@pytest.mark.asyncio
async def test_browser_tool_static_http_error():
    tool = BrowserTool()
    with patch("vibe.tools.browser.is_safe_url", return_value=True):
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"
            mock_response.content = b"Not Found"
            mock_get.return_value = mock_response

            result = await tool.execute(url="https://example.com/not-found")
            assert not result.success
            assert "HTTP 404" in result.error


# ===========================================================================
# BrowserTool Execution Tests (Tier 2: Dynamic Playwright)
# ===========================================================================


@pytest.mark.asyncio
async def test_browser_tool_dynamic_fallback_when_playwright_missing():
    tool = BrowserTool()
    fake_html = "<html><body><p>Static content</p></body></html>"
    with patch("vibe.tools.browser.is_safe_url", return_value=True):
        with patch("vibe.tools.browser.HAS_PLAYWRIGHT", False):
            # When action is 'read', falls back to static
            with patch("httpx.AsyncClient.get") as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.text = fake_html
                mock_response.content = fake_html.encode("utf-8")
                mock_get.return_value = mock_response

                result = await tool.execute(
                    url="https://example.com", mode="dynamic", action="read"
                )
                assert result.success
                assert "Static content" in result.content

            # When action is 'click', returns informative error
            click_result = await tool.execute(
                url="https://example.com", mode="dynamic", action="click", selector="#btn"
            )
            assert not click_result.success
            assert "Playwright is required" in click_result.error


@pytest.mark.asyncio
async def test_browser_tool_dynamic_execution_with_mock_playwright():
    tool = BrowserTool()

    mock_page = AsyncMock()
    mock_page.content.return_value = (
        "<html><body><h1>Rendered by JS</h1><p>Dynamic text</p></body></html>"
    )
    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_playwright = AsyncMock()
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch("vibe.tools.browser.is_safe_url", return_value=True):
        with patch("vibe.tools.browser.HAS_PLAYWRIGHT", True):
            mock_runner = AsyncMock(return_value=mock_page.content.return_value)
            with patch("vibe.tools.browser._run_playwright", new=mock_runner):
                result = await tool.execute(
                    url="https://example.com/app", mode="dynamic", action="read"
                )
                assert result.success
                assert "# Rendered by JS" in result.content
                assert result.metadata.get("tier") == "dynamic"


def test_ssrf_guard_blocks_ipv6_mapped_ipv4():
    # IPv6 mapped IPv4 loopback & private
    assert not is_safe_url("http://[::ffff:127.0.0.1]/secret")
    assert not is_safe_url("http://[::ffff:169.254.169.254]/latest/meta-data")
    assert not is_safe_url("http://[::ffff:192.168.1.1]/admin")
    # CGNAT and Cloud Metadata
    assert not is_safe_url("http://100.64.0.1/status")
    assert not is_safe_url("http://100.100.100.200/latest/meta-data")


@pytest.mark.asyncio
async def test_browser_tool_blocks_redirect_to_private_ip():
    tool = BrowserTool()
    # Initial request redirects to 169.254.169.254
    with patch("httpx.AsyncClient.get") as mock_get:
        redirect_response = MagicMock()
        redirect_response.is_redirect = True
        redirect_response.headers = {"location": "http://169.254.169.254/latest/meta-data"}
        mock_get.return_value = redirect_response

        # Allow initial URL but redirect should be blocked by SSRFGuard
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            result = await tool.execute(url="https://example.com/redirect-to-metadata")
            assert not result.success
            assert "Blocked by safety policy (SSRF)" in result.error


@pytest.mark.asyncio
async def test_browser_tool_static_mode_click_error():
    tool = BrowserTool()
    with patch("vibe.tools.browser.is_safe_url", return_value=True):
        result = await tool.execute(url="https://example.com", mode="static", action="click")
        assert not result.success
        assert "Cannot perform interactive 'click' action in 'static' mode" in result.error


def test_browser_tool_registered_in_factory():
    """QueryLoopFactory must register 'browse' and 'fetch_url' in default ToolSystem."""
    from vibe.core.query_loop_factory import QueryLoopFactory

    factory = QueryLoopFactory(base_url="http://localhost:11434/v1", model="test-model")
    tool_system = factory.create_tool_system()
    assert "browse" in tool_system.list_tools()
    assert "fetch_url" in tool_system.list_tools()


@pytest.mark.asyncio
async def test_playwright_route_interception_aborts_unsafe_requests():
    from vibe.tools.browser import _run_playwright

    mock_page = AsyncMock()
    mock_page.content.return_value = "<html><body>Clean</body></html>"
    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    intercepted_handler = None

    async def mock_route(pattern, handler):
        nonlocal intercepted_handler
        intercepted_handler = handler

    mock_page.route = AsyncMock(side_effect=mock_route)

    mock_p = MagicMock()
    mock_p.chromium.launch = AsyncMock(return_value=mock_browser)

    class MockAsyncPlaywright:
        async def __aenter__(self):
            return mock_p

        async def __aexit__(self, *args):
            pass

    mock_playwright_mod = MagicMock()
    mock_playwright_mod.async_api.async_playwright = MagicMock(return_value=MockAsyncPlaywright())
    with patch.dict(
        "sys.modules",
        {
            "playwright": mock_playwright_mod,
            "playwright.async_api": mock_playwright_mod.async_api,
        },
    ):
        content = await _run_playwright("https://example.com/app")
        assert content == "<html><body>Clean</body></html>"
        assert intercepted_handler is not None

        # Test route handler aborts unsafe request
        unsafe_route = AsyncMock()
        unsafe_req = MagicMock(url="http://169.254.169.254/latest/meta-data")
        await intercepted_handler(unsafe_route, unsafe_req)
        unsafe_route.abort.assert_awaited_once_with("blockedbyclient")

        # Test route handler allows safe request
        safe_route = AsyncMock()
        safe_req = MagicMock(url="https://example.com/assets/app.js")
        with patch("vibe.tools.browser.is_safe_url", return_value=True):
            await intercepted_handler(safe_route, safe_req)
            safe_route.continue_.assert_awaited_once()
