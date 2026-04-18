"""Basic import smoke tests."""

import pytest


def test_import_model_gateway():
    from vibe.core.model_gateway import LLMClient, LLMResponse, ErrorType
    from vibe.core.error_recovery import ErrorRecovery, RetryPolicy

    assert LLMClient is not None


def test_import_tools():
    from vibe.tools.tool_system import ToolSystem, Tool, ToolResult
    from vibe.tools.bash import BashTool, BashSandbox
    from vibe.tools.file import ReadFileTool, WriteFileTool

    assert ToolSystem is not None


def test_import_query_loop():
    from vibe.core.query_loop import QueryLoop, QueryResult, Message
    from vibe.core.context_compactor import ContextCompactor

    assert QueryLoop is not None


def test_import_memory():
    from vibe.harness.memory.trace_store import TraceStore
    from vibe.harness.memory.eval_store import EvalStore

    assert TraceStore is not None


def test_import_delegate():
    from vibe.harness.orchestration.sync_delegate import SyncDelegate

    assert SyncDelegate is not None
