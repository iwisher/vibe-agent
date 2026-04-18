"""Tests for vibe.core.error_recovery."""

import asyncio

import pytest

from vibe.core.error_recovery import ErrorRecovery, RetryPolicy


@pytest.mark.asyncio
async def test_execute_with_retry_success():
    policy = RetryPolicy(max_retries=2, initial_delay=0.01)
    recovery = ErrorRecovery(policy)

    async def ok():
        return "success"

    result = await recovery.execute_with_retry(ok)
    assert result == "success"


@pytest.mark.asyncio
async def test_execute_with_retry_eventual_success():
    policy = RetryPolicy(max_retries=3, initial_delay=0.01)
    recovery = ErrorRecovery(policy)

    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("fail")
        return "ok"

    result = await recovery.execute_with_retry(flaky)
    assert result == "ok"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_execute_with_retry_exhausted():
    policy = RetryPolicy(max_retries=1, initial_delay=0.01)
    recovery = ErrorRecovery(policy)

    async def always_fail():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await recovery.execute_with_retry(always_fail)


@pytest.mark.asyncio
async def test_execute_with_retry_non_retryable():
    policy = RetryPolicy(max_retries=3, initial_delay=0.01, retryable_exceptions=(RuntimeError,))
    recovery = ErrorRecovery(policy)

    async def fail():
        raise ValueError("not retryable")

    with pytest.raises(ValueError, match="not retryable"):
        await recovery.execute_with_retry(fail)


def test_handle_error_hints():
    recovery = ErrorRecovery(RetryPolicy())
    assert "timed out" in recovery.handle_error(Exception("timeout"))
    assert "Rate limit" in recovery.handle_error(Exception("rate limit 429"))
    assert "Authentication" in recovery.handle_error(Exception("auth failed 401"))
