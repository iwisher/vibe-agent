"""Tests for MCPBridge HTTP connection pooling."""

import pytest

from vibe.tools.mcp_bridge import MCPBridge


class FakeClient:
    """Fake httpx client that tracks creation count."""
    _instances = 0

    def __init__(self, timeout=None):
        FakeClient._instances += 1
        self.timeout = timeout

    async def post(self, url, json):
        class Resp:
            def raise_for_status(self): pass
            def json(self): return {"result": 42}
        return Resp()

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def reset_fake_client():
    FakeClient._instances = 0
    yield


@pytest.mark.asyncio
async def test_mcp_bridge_reuses_http_client(monkeypatch):
    """Connection pooling: multiple calls to same URL reuse the client."""
    import vibe.tools.mcp_bridge as mcp_module
    monkeypatch.setattr(mcp_module, "httpx", type("FakeHttpx", (), {"AsyncClient": FakeClient})())

    bridge = MCPBridge(configs=[
        {
            "name": "calc",
            "description": "Calculator",
            "url": "http://localhost:3000/call",
            "tools": [
                {"name": "add", "description": "Add", "parameters": {"type": "object"}},
            ],
        }
    ])

    await bridge.execute_tool("add", a=1, b=2)
    await bridge.execute_tool("add", a=3, b=4)

    assert FakeClient._instances == 1, "Expected HTTP client to be reused (pooled)"


@pytest.mark.asyncio
async def test_mcp_bridge_creates_client_per_url(monkeypatch):
    """Different URLs get different clients."""
    import vibe.tools.mcp_bridge as mcp_module
    monkeypatch.setattr(mcp_module, "httpx", type("FakeHttpx", (), {"AsyncClient": FakeClient})())

    bridge = MCPBridge(configs=[
        {
            "name": "svc1",
            "url": "http://host1/call",
            "tools": [{"name": "t1", "parameters": {"type": "object"}}],
        },
        {
            "name": "svc2",
            "url": "http://host2/call",
            "tools": [{"name": "t2", "parameters": {"type": "object"}}],
        },
    ])

    await bridge.execute_tool("t1")
    await bridge.execute_tool("t2")

    assert FakeClient._instances == 2, "Expected separate clients per URL"


@pytest.mark.asyncio
async def test_mcp_bridge_close_clears_clients(monkeypatch):
    """close() clears the client cache."""
    import vibe.tools.mcp_bridge as mcp_module
    monkeypatch.setattr(mcp_module, "httpx", type("FakeHttpx", (), {"AsyncClient": FakeClient})())

    bridge = MCPBridge(configs=[
        {
            "name": "calc",
            "url": "http://localhost:3000/call",
            "tools": [{"name": "add", "parameters": {"type": "object"}}],
        }
    ])

    await bridge.execute_tool("add")
    assert len(bridge._http_clients) == 1

    await bridge.close()
    assert len(bridge._http_clients) == 0
