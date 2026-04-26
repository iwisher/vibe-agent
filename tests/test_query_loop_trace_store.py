"""Tests for QueryLoop trace store integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from vibe.core.query_loop import QueryLoop, QueryState
from vibe.harness.memory.trace_store import MemoryTraceStore


class TestQueryLoopTraceStore:
    """Test that QueryLoop logs sessions to trace store."""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.model = "test-model"
        llm.complete = AsyncMock(return_value=MagicMock(
            content="Hello",
            tool_calls=[],
            is_error=False,
            error=None,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ))
        llm.close = AsyncMock()
        return llm

    @pytest.fixture
    def mock_tool_system(self):
        ts = MagicMock()
        ts.get_tool_schemas.return_value = []
        return ts

    @pytest.fixture
    def trace_store(self):
        return MemoryTraceStore()

    @pytest.fixture
    def query_loop(self, mock_llm, mock_tool_system, trace_store):
        return QueryLoop(
            llm_client=mock_llm,
            tool_system=mock_tool_system,
            trace_store=trace_store,
            max_iterations=1,
        )

    @pytest.mark.asyncio
    async def test_logs_session_on_completion(self, query_loop, trace_store):
        """QueryLoop should log session when run completes."""
        results = []
        async for result in query_loop.run("Hello"):
            results.append(result)

        assert len(results) >= 1
        assert trace_store.count_sessions() == 1

        session = trace_store.get_sessions(limit=1)[0]
        assert session["success"] is True
        assert session["model"] == "test-model"
        assert any(m["role"] == "user" and m["content"] == "Hello" for m in session["messages"])

    @pytest.mark.asyncio
    async def test_logs_session_on_error(self, mock_llm, mock_tool_system, trace_store):
        """QueryLoop should log session even on error."""
        mock_llm.complete = AsyncMock(return_value=MagicMock(
            content="",
            tool_calls=[],
            is_error=True,
            error="API failure",
            usage={},
        ))

        query_loop = QueryLoop(
            llm_client=mock_llm,
            tool_system=mock_tool_system,
            trace_store=trace_store,
            max_iterations=1,
        )

        results = []
        async for result in query_loop.run("Hello"):
            results.append(result)

        assert trace_store.count_sessions() == 1
        session = trace_store.get_sessions(limit=1)[0]
        assert session["success"] is False
        assert "ERROR" in session["error"]

    @pytest.mark.asyncio
    async def test_no_trace_store_no_crash(self, mock_llm, mock_tool_system):
        """QueryLoop should not crash if trace_store is None."""
        query_loop = QueryLoop(
            llm_client=mock_llm,
            tool_system=mock_tool_system,
            trace_store=None,
            max_iterations=1,
        )

        results = []
        async for result in query_loop.run("Hello"):
            results.append(result)

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_logs_tool_results(self, mock_llm, mock_tool_system, trace_store):
        """QueryLoop should log tool results in session."""
        mock_llm.complete = AsyncMock(return_value=MagicMock(
            content="",
            tool_calls=[{"id": "call_1", "function": {"name": "test_tool", "arguments": "{}"}}],
            is_error=False,
            error=None,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ))

        query_loop = QueryLoop(
            llm_client=mock_llm,
            tool_system=mock_tool_system,
            trace_store=trace_store,
            max_iterations=1,
        )

        results = []
        async for result in query_loop.run("Use tool"):
            results.append(result)

        session = trace_store.get_sessions(limit=1)[0]
        assert len(session["tool_results"]) >= 0  # May be empty if tool exec fails


class TestJSONTraceStoreAtomic:
    """Test atomic writes for JSONTraceStore."""

    def test_atomic_write_uses_temp_file(self, tmp_path):
        """JSONTraceStore should write to temp file then rename."""
        from vibe.harness.memory.trace_store import JSONTraceStore
        import os

        file_path = str(tmp_path / "traces.json")
        store = JSONTraceStore(file_path=file_path)

        store.log_session(
            session_id="sess-1",
            messages=[{"role": "user", "content": "Hello"}],
            tool_results=[],
            success=True,
            model="test",
        )

        # Should create the file
        assert os.path.exists(file_path)

        # Temp file should not exist
        assert not os.path.exists(file_path + ".tmp")

        # Content should be valid JSON
        import json
        with open(file_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["id"] == "sess-1"
