"""Tests for vibe.core.model_gateway.CircuitBreaker."""

import time

from vibe.core.model_gateway import CircuitBreaker


def test_cb_001_starts_closed():
    cb = CircuitBreaker()
    assert not cb.is_open("model-a")


def test_cb_002_opens_after_threshold_failures():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("model-a")
    cb.record_failure("model-a")
    assert not cb.is_open("model-a")  # 2 < 3
    cb.record_failure("model-a")
    assert cb.is_open("model-a")  # 3 >= 3


def test_cb_003_success_resets_counter():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("model-a")
    cb.record_failure("model-a")
    cb.record_success("model-a")
    cb.record_failure("model-a")
    assert not cb.is_open("model-a")  # counter reset, now at 1


def test_cb_004_half_open_after_cooldown():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=0.1)
    cb.record_failure("model-a")
    cb.record_failure("model-a")
    assert cb.is_open("model-a")
    time.sleep(0.15)
    assert not cb.is_open("model-a")  # half-open


def test_cb_005_reopens_on_half_open_failure():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=0.1)
    cb.record_failure("model-a")
    cb.record_failure("model-a")
    assert cb.is_open("model-a")
    time.sleep(0.15)
    assert not cb.is_open("model-a")  # half-open
    cb.record_failure("model-a")
    assert cb.is_open("model-a")  # re-opened


def test_cb_006_isolated_per_model():
    cb = CircuitBreaker(threshold=2)
    cb.record_failure("model-a")
    cb.record_failure("model-a")
    assert cb.is_open("model-a")
    assert not cb.is_open("model-b")


def test_cb_007_success_closes_breaker():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=0.1)
    cb.record_failure("model-a")
    cb.record_failure("model-a")
    assert cb.is_open("model-a")
    time.sleep(0.15)
    assert not cb.is_open("model-a")  # half-open
    cb.record_success("model-a")
    assert not cb.is_open("model-a")  # closed
    # Should not re-open immediately on next single failure
    cb.record_failure("model-a")
    assert not cb.is_open("model-a")
