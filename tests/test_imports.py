"""Basic import smoke tests."""



def test_import_model_gateway():
    from vibe.core.model_gateway import LLMClient

    assert LLMClient is not None


def test_import_tools():
    from vibe.tools.tool_system import ToolSystem

    assert ToolSystem is not None


def test_import_query_loop():
    from vibe.core.query_loop import QueryLoop

    assert QueryLoop is not None


def test_import_memory():
    from vibe.harness.memory.trace_store import TraceStore

    assert TraceStore is not None


def test_import_delegate():
    from vibe.harness.orchestration.sync_delegate import SyncDelegate

    assert SyncDelegate is not None
