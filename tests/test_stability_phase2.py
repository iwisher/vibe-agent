"""Phase 2 stability tests: resource lifecycle, state machine, schema migration."""

import asyncio
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.model_gateway import LLMClient, LLMResponse, CircuitBreaker
from vibe.core.query_loop import QueryLoop, QueryState
from vibe.harness.memory.eval_store import EvalStore, EvalResult
from vibe.tools.tool_system import ToolSystem


# ─── QueryLoop State Machine ───

@pytest.mark.asyncio
async def test_queryloop_001_incomplete_on_max_iterations():
    """QueryLoop should set INCOMPLETE state when max_iterations is reached."""
    llm = MagicMock(spec=LLMClient)
    llm.model = "test-model"
    llm.close = AsyncMock()
    # Each call returns a tool call, forcing another iteration
    llm.complete = AsyncMock(
        side_effect=lambda msgs, tools=None: LLMResponse(
            content="",
            tool_calls=[{"id": "1", "type": "function", "function": {"name": "fake_tool", "arguments": "{}"}}],
        )
    )

    tools = MagicMock(spec=ToolSystem)
    tools.get_tool_schemas.return_value = []
    tools.execute_tool = AsyncMock(return_value=MagicMock(success=True, content="ok", error=None))

    loop = QueryLoop(llm_client=llm, tool_system=tools, max_iterations=3)
    results = []
    async for r in loop.run(initial_query="test"):
        results.append(r)

    assert loop.state == QueryState.INCOMPLETE


@pytest.mark.asyncio
async def test_queryloop_002_completed_on_natural_break():
    """QueryLoop should set COMPLETED when response has no tool calls."""
    llm = MagicMock(spec=LLMClient)
    llm.model = "test-model"
    llm.close = AsyncMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(content="done", tool_calls=None)
    )

    tools = MagicMock(spec=ToolSystem)
    tools.get_tool_schemas.return_value = []

    loop = QueryLoop(llm_client=llm, tool_system=tools, max_iterations=50)
    results = []
    async for r in loop.run(initial_query="test"):
        results.append(r)

    assert loop.state == QueryState.COMPLETED
    assert len(results) == 1
    assert results[0].response == "done"


# ─── Resource Lifecycle ───

@pytest.mark.asyncio
async def test_queryloop_003_close_releases_llm_client():
    """QueryLoop.close() should call LLMClient.close()."""
    llm = MagicMock(spec=LLMClient)
    llm.close = AsyncMock()

    tools = MagicMock(spec=ToolSystem)
    loop = QueryLoop(llm_client=llm, tool_system=tools)
    await loop.close()

    llm.close.assert_awaited_once()


# ─── EvalStore Schema Migration ───

class TestEvalStoreSchemaMigration:
    """Tests for EvalStore schema migration (total_tokens + latency_seconds)."""

    def test_evalstore_001_new_db_has_columns(self):
        """Fresh database should include total_tokens and latency_seconds columns."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "evals.db"
            store = EvalStore(db_path=str(db_path))

            with sqlite3.connect(db_path) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_results)")}

            assert "total_tokens" in cols
            assert "latency_seconds" in cols

    def test_evalstore_002_migration_adds_columns_to_old_db(self):
        """Existing database without columns should be migrated."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "evals.db"
            # Create old-style database
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE eval_results (id INTEGER PRIMARY KEY, eval_id TEXT, passed INTEGER, diff TEXT, timestamp TEXT)"
                )

            store = EvalStore(db_path=str(db_path))

            with sqlite3.connect(db_path) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_results)")}

            assert "total_tokens" in cols
            assert "latency_seconds" in cols

    def test_evalstore_003_record_result_persists_all_fields(self):
        """record_result should persist total_tokens and latency_seconds."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "evals.db"
            store = EvalStore(db_path=str(db_path))

            result = EvalResult(
                eval_id="test-case",
                passed=True,
                total_tokens=1234,
                latency_seconds=2.5,
            )
            store.record_result(result)

            rows = store.get_results("test-case")
            assert len(rows) == 1
            assert rows[0]["total_tokens"] == 1234
            assert rows[0]["latency_seconds"] == 2.5


# ─── Observability Singleton Identity ───

class TestObservabilitySingleton:
    """Tests for Observability double-default bug fix."""

    def test_observability_001_module_level_is_get_default(self):
        """The module-level `obs` should be the same object as get_default()."""
        from vibe.evals.observability import Observability, obs

        default = Observability.get_default()
        assert obs is default
