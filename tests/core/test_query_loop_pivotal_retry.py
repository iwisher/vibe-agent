"""Unit tests for pivotal local retry in QueryLoop (Workstream C, PivoARL).

Covers: repeated-identical-failure detection (`_pivotal_turn`), the single
guided retry per call signature, security-denial exclusion (C2), config
gating (C3), budget gating, and never-raises fallback behavior.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.config import ErrorRecoveryConfig
from vibe.core.coordinators import SecurityCheckResult
from vibe.core.llm_types import ErrorType, LLMResponse
from vibe.core.query_loop import QueryLoop, QueryState
from vibe.tools._utils import extract_tool_call_arguments
from vibe.tools.tool_system import ToolResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, args: dict | str, call_id: str = "call_1") -> dict:
    if isinstance(args, dict):
        args = json.dumps(args)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


_BAD_CALL = _tool_call("bash", {"command": "bad"})
_BAD_CALL_DICT_ARGS = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "bash", "arguments": {"command": "bad"}},
}
_GOOD_CALL = _tool_call("bash", {"command": "good"})


def _make_llm(*responses) -> MagicMock:
    """LLM mock whose complete() returns/raises the given items in order."""
    client = MagicMock()
    client.complete = AsyncMock(side_effect=list(responses))
    client.model = "test-model"
    return client


def _make_loop(llm, config=None, max_iterations: int = 10) -> QueryLoop:
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[])
    loop = QueryLoop(
        llm_client=llm,
        tool_system=tools,
        config=config,
        max_iterations=max_iterations,
        stream=False,
    )
    # Bypass the 5-layer security coordinator unless a test overrides it.
    loop.security_coord = None
    return loop


def _fail_all(calls, session_id=None):
    return [
        ToolResult(success=False, content=None, error="command failed with exit 1") for _ in calls
    ]


def _fail_bad_only(calls, session_id=None):
    results = []
    for call in calls:
        args = extract_tool_call_arguments(call)
        if args.get("command") == "bad":
            results.append(
                ToolResult(success=False, content=None, error="command failed with exit 1")
            )
        else:
            results.append(ToolResult(success=True, content="ok"))
    return results


async def _drain(loop: QueryLoop) -> list:
    return [r async for r in loop.run("do the thing") if not r.is_status]


def _guidance_messages(loop: QueryLoop) -> list:
    return [m for m in loop.messages if m.role == "system" and "PIVOTAL RETRY" in m.content]


# ---------------------------------------------------------------------------
# Detection: repeated identical failures mark the pivotal turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_identical_failure_sets_pivotal_turn():
    # Second failure uses dict-shaped arguments: normalization must still
    # recognize it as the identical call.
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL_DICT_ARGS]),
        LLMResponse(content="giving up on that call"),
        LLMResponse(content="final answer"),
    )
    loop = _make_loop(llm)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    results = await _drain(loop)

    assert loop._pivotal_turn == 2  # second identical failure, iteration 2
    assert loop.state == QueryState.COMPLETED
    assert results[-1].error is None
    # 3 organic iterations + 1 guided retry
    assert llm.complete.call_count == 4
    assert len(_guidance_messages(loop)) == 1


@pytest.mark.asyncio
async def test_distinct_failures_do_not_set_pivotal_turn():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_tool_call("bash", {"command": "a"})]),
        LLMResponse(content="", tool_calls=[_tool_call("bash", {"command": "b"})]),
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    await _drain(loop)

    assert loop._pivotal_turn is None
    assert llm.complete.call_count == 3  # no guided retry
    assert _guidance_messages(loop) == []


# ---------------------------------------------------------------------------
# Guided retry: once per signature, then normal degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guided_retry_fires_once_then_normal_degradation():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),  # guided retry answer
        LLMResponse(content="", tool_calls=[_BAD_CALL]),  # organic iteration 3
    )
    loop = _make_loop(llm, max_iterations=3)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    await _drain(loop)

    assert loop._pivotal_turn == 2
    # 3 organic iterations + exactly 1 guided retry despite continued failures
    assert llm.complete.call_count == 4
    assert len(_guidance_messages(loop)) == 1
    # Second identical failure after the guided retry degrades normally
    assert loop.state == QueryState.INCOMPLETE


@pytest.mark.asyncio
async def test_guided_retry_success_continues_to_completed():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_GOOD_CALL]),  # corrected call
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_bad_only)

    results = await _drain(loop)

    assert loop._pivotal_turn == 2
    assert loop.state == QueryState.COMPLETED
    assert results[-1].response == "done"
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert any(m.content == "ok" for m in tool_msgs)
    # Guidance names the failed tool, arguments, and error
    guidance = _guidance_messages(loop)[0].content
    assert "bash" in guidance and "bad" in guidance and "exit 1" in guidance


# ---------------------------------------------------------------------------
# C2: security denials are final — never detected, never retried
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_denial_via_coordinator_never_retried():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm)
    denial = SecurityCheckResult(
        allowed=False, reason="Critical pattern detected: rm -rf", layer="pattern_scan"
    )
    loop.security_coord = MagicMock()
    loop.security_coord.evaluate_tool_call = MagicMock(return_value=denial)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    results = await _drain(loop)

    assert loop._pivotal_turn is None
    assert llm.complete.call_count == 3  # no guided retry
    assert _guidance_messages(loop) == []
    assert results[-1].state == QueryState.COMPLETED


@pytest.mark.asyncio
async def test_security_denial_via_error_prefix_never_retried():
    def _deny(calls, session_id=None):
        return [
            ToolResult(
                success=False,
                content=None,
                error="Command blocked by safety policy (matched pattern: rm -rf).",
            )
            for _ in calls
        ]

    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm)
    loop.tool_executor.execute = AsyncMock(side_effect=_deny)

    await _drain(loop)

    assert loop._pivotal_turn is None
    assert llm.complete.call_count == 3
    assert _guidance_messages(loop) == []


# ---------------------------------------------------------------------------
# C3: config gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_via_config_matches_previous_behavior():
    config = SimpleNamespace(
        error_recovery=ErrorRecoveryConfig(pivotal_retry_enabled=False),
        query_loop=None,
        retry=None,
        memory=None,
    )
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm, config=config)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    results = await _drain(loop)

    assert loop._pivotal_retry_enabled is False
    assert loop._pivotal_turn is None
    assert llm.complete.call_count == 3  # identical to pre-Workstream-C behavior
    assert _guidance_messages(loop) == []
    assert results[-1].state == QueryState.COMPLETED


# ---------------------------------------------------------------------------
# Iteration budget gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_guided_retry_when_budget_exhausted():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
    )
    loop = _make_loop(llm, max_iterations=2)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    await _drain(loop)

    # Detection still marks the pivotal turn (consumed by reflection), but no
    # guided retry is attempted once the iteration budget is exhausted.
    assert loop._pivotal_turn == 2
    assert llm.complete.call_count == 2
    assert _guidance_messages(loop) == []
    assert loop.state == QueryState.INCOMPLETE


# ---------------------------------------------------------------------------
# Never-raises: guided-retry failures fall back to normal behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_exception_during_guided_retry_falls_back():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        RuntimeError("guided retry exploded"),
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    results = await _drain(loop)

    assert loop._pivotal_turn == 2
    assert loop.state == QueryState.COMPLETED
    assert results[-1].error is None
    assert results[-1].response == "done"


@pytest.mark.asyncio
async def test_llm_error_response_during_guided_retry_falls_back():
    llm = _make_llm(
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", tool_calls=[_BAD_CALL]),
        LLMResponse(content="", error="boom", error_type=ErrorType.SERVER_ERROR),
        LLMResponse(content="done"),
    )
    loop = _make_loop(llm)
    loop.tool_executor.execute = AsyncMock(side_effect=_fail_all)

    results = await _drain(loop)

    assert loop._pivotal_turn == 2
    assert loop.state == QueryState.COMPLETED
    assert results[-1].response == "done"
