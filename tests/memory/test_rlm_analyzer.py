"""Unit tests for RLMThresholdAnalyzer — Phase 2 telemetry-triggered activation.

Covers: trigger when compaction % exceeds threshold, no-trigger when insufficient
sessions, no-trigger when metrics below threshold, mocked TelemetryCollector.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from vibe.memory.rlm_analyzer import RLMThresholdAnalyzer, RLMTriggerDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_config():
    """Return a mock config with RLM thresholds."""
    cfg = MagicMock()
    cfg.trigger_window_sessions = 50
    cfg.min_sessions_before_trigger = 10
    cfg.trigger_threshold_chars = 100_000
    cfg.trigger_threshold_compaction_pct = 0.3
    return cfg


@pytest.fixture
def fake_telemetry_db():
    """Return a mock telemetry DB with execute/fetchone interface."""
    db = MagicMock()
    db.conn = MagicMock()
    return db


@pytest.fixture
def fake_telemetry(fake_telemetry_db):
    tel = MagicMock()
    tel.db = fake_telemetry_db
    return tel


def _make_session_row(session_id: str, total_chars: int, duration: float) -> tuple:
    """Helper to build a session telemetry row."""
    return (json.dumps({
        "session_id": session_id,
        "total_chars": total_chars,
        "duration_seconds": duration,
    }),)


# ---------------------------------------------------------------------------
# Trigger conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_when_compaction_pct_exceeds_threshold(fake_telemetry, fake_config, fake_telemetry_db):
    """RLM should trigger when compaction % >= threshold."""
    # 20 sessions, 8 with compaction → 40% compaction rate
    session_rows = [
        _make_session_row(f"sess-{i:03d}", 50_000, 10.0)
        for i in range(20)
    ]

    def mock_execute(query, params=None):
        cursor = MagicMock()
        if "type = 'session'" in query:
            cursor.fetchall = MagicMock(return_value=session_rows)
        elif "type = 'compaction'" in query:
            # 8 sessions had compaction
            cursor.fetchone = MagicMock(return_value=(8,))
        else:
            cursor.fetchall = MagicMock(return_value=[])
            cursor.fetchone = MagicMock(return_value=(0,))
        return cursor

    fake_telemetry_db.conn.execute = mock_execute
    analyzer = RLMThresholdAnalyzer(fake_telemetry, fake_config)
    decision = await analyzer.analyze()

    assert decision.should_trigger is True
    assert "compaction" in decision.reason.lower() or "40%" in decision.reason


@pytest.mark.asyncio
async def test_no_trigger_when_insufficient_sessions(fake_telemetry, fake_config, fake_telemetry_db):
    """RLM should NOT trigger when total sessions < min_sessions_before_trigger."""
    # Only 5 sessions (< 10 min)
    session_rows = [
        _make_session_row(f"sess-{i:03d}", 200_000, 10.0)
        for i in range(5)
    ]

    def mock_execute(query, params=None):
        cursor = MagicMock()
        if "type = 'session'" in query:
            cursor.fetchall = MagicMock(return_value=session_rows)
        else:
            cursor.fetchone = MagicMock(return_value=(0,))
        return cursor

    fake_telemetry_db.conn.execute = mock_execute
    analyzer = RLMThresholdAnalyzer(fake_telemetry, fake_config)
    decision = await analyzer.analyze()

    assert decision.should_trigger is False
    assert "insufficient" in decision.reason.lower()


@pytest.mark.asyncio
async def test_no_trigger_when_metrics_below_threshold(fake_telemetry, fake_config, fake_telemetry_db):
    """RLM should NOT trigger when all metrics are within thresholds."""
    # 15 sessions, all below char threshold, 0% compaction
    session_rows = [
        _make_session_row(f"sess-{i:03d}", 10_000, 5.0)
        for i in range(15)
    ]

    def mock_execute(query, params=None):
        cursor = MagicMock()
        if "type = 'session'" in query:
            cursor.fetchall = MagicMock(return_value=session_rows)
        elif "type = 'compaction'" in query:
            cursor.fetchone = MagicMock(return_value=(0,))
        else:
            cursor.fetchone = MagicMock(return_value=(0,))
        return cursor

    fake_telemetry_db.conn.execute = mock_execute
    analyzer = RLMThresholdAnalyzer(fake_telemetry, fake_config)
    decision = await analyzer.analyze()

    assert decision.should_trigger is False
    assert "within thresholds" in decision.reason.lower()


@pytest.mark.asyncio
async def test_trigger_when_chars_exceed_threshold(fake_telemetry, fake_config, fake_telemetry_db):
    """RLM should trigger when sessions exceed char threshold."""
    # 15 sessions, 3 exceed 100K chars
    session_rows = [
        _make_session_row(f"sess-{i:03d}", 150_000 if i < 3 else 50_000, 5.0)
        for i in range(15)
    ]

    def mock_execute(query, params=None):
        cursor = MagicMock()
        if "type = 'session'" in query:
            cursor.fetchall = MagicMock(return_value=session_rows)
        elif "type = 'compaction'" in query:
            cursor.fetchone = MagicMock(return_value=(0,))
        else:
            cursor.fetchone = MagicMock(return_value=(0,))
        return cursor

    fake_telemetry_db.conn.execute = mock_execute
    analyzer = RLMThresholdAnalyzer(fake_telemetry, fake_config)
    decision = await analyzer.analyze()

    assert decision.should_trigger is True
    assert "chars" in decision.reason.lower() or "exceeded" in decision.reason.lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_trigger_when_telemetry_db_is_none(fake_config):
    """If telemetry DB is None, should return safe default (no trigger)."""
    tel = MagicMock()
    tel.db = None
    analyzer = RLMThresholdAnalyzer(tel, fake_config)
    decision = await analyzer.analyze()

    assert decision.should_trigger is False
    assert decision.metrics["total_sessions"] == 0


@pytest.mark.asyncio
async def test_no_trigger_when_telemetry_is_none(fake_config):
    """If telemetry is None, should return safe default (no trigger)."""
    analyzer = RLMThresholdAnalyzer(None, fake_config)
    decision = await analyzer.analyze()

    assert decision.should_trigger is False


@pytest.mark.asyncio
async def test_analyzer_never_raises(fake_telemetry, fake_config, fake_telemetry_db):
    """Analyzer should never raise — returns safe defaults on error."""
    from unittest.mock import AsyncMock
    analyzer = RLMThresholdAnalyzer(fake_telemetry, fake_config)
    analyzer._query_session_stats = AsyncMock(side_effect=RuntimeError("Test crash"))
    # Should NOT raise
    decision = await analyzer.analyze()
    assert decision.should_trigger is False
    assert "error" in decision.reason.lower()


@pytest.mark.asyncio
async def test_metrics_populated_correctly(fake_telemetry, fake_config, fake_telemetry_db):
    """Decision metrics should reflect actual query results."""
    session_rows = [
        _make_session_row(f"sess-{i:03d}", 50_000, 10.0)
        for i in range(20)
    ]

    def mock_execute(query, params=None):
        cursor = MagicMock()
        if "type = 'session'" in query:
            cursor.fetchall = MagicMock(return_value=session_rows)
        elif "type = 'compaction'" in query:
            cursor.fetchone = MagicMock(return_value=(5,))
        else:
            cursor.fetchone = MagicMock(return_value=(0,))
        return cursor

    fake_telemetry_db.conn.execute = mock_execute
    analyzer = RLMThresholdAnalyzer(fake_telemetry, fake_config)
    decision = await analyzer.analyze()

    assert decision.metrics["total_sessions"] == 20
    assert decision.metrics["sessions_above_char_threshold"] == 0
    assert decision.metrics["compaction_session_pct"] == 5 / 20
    assert decision.metrics["avg_duration_seconds"] == 10.0
