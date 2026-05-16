"""Tests for TelemetryCollector — decoupled telemetry access."""

import pytest

from vibe.memory.telemetry_collector import TelemetryCollector, TelemetrySummary


class FakeDB:
    """Fake database for testing TelemetryCollector."""

    def __init__(self):
        self.conn = FakeConn()


class FakeConn:
    """Fake SQLite connection."""

    def __init__(self):
        self._data = []
        self._committed = False

    def execute(self, sql, params=None):
        # Simulate telemetry queries
        if "session" in sql.lower() and "count" in sql.lower():
            return FakeCursor((5, 12.5))
        elif "compaction" in sql.lower():
            return FakeCursor((2,))
        elif "error" in sql.lower():
            return FakeCursor((1,))
        else:
            self._data.append((sql, params))
            return FakeCursor((1,))

    def commit(self):
        self._committed = True


class FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class TestTelemetryCollector:
    def test_get_summary_no_db(self):
        collector = TelemetryCollector()
        summary = collector.get_summary(hours=24)
        assert summary.sessions_count == 0
        assert summary.avg_duration_seconds == 0.0

    def test_get_summary_with_db(self):
        db = FakeDB()
        collector = TelemetryCollector(db)
        summary = collector.get_summary(hours=24)
        assert summary.sessions_count == 5
        assert summary.avg_duration_seconds == 12.5
        assert summary.compactions_count == 2
        assert summary.errors_count == 1

    def test_record_session(self):
        db = FakeDB()
        collector = TelemetryCollector(db)
        assert collector.record_session("sess_1", 10.5, {"tool": "test"}) is True
        assert db.conn._committed is True

    def test_record_session_no_db(self):
        collector = TelemetryCollector()
        assert collector.record_session("sess_1", 10.5) is False

    def test_record_compaction(self):
        db = FakeDB()
        collector = TelemetryCollector(db)
        assert collector.record_compaction(10) is True
        assert db.conn._committed is True

    def test_record_compaction_no_db(self):
        collector = TelemetryCollector()
        assert collector.record_compaction(10) is False

    def test_set_db(self):
        collector = TelemetryCollector()
        assert collector.get_summary().sessions_count == 0
        db = FakeDB()
        collector.set_db(db)
        summary = collector.get_summary()
        assert summary.sessions_count == 5

    def test_summary_dataclass(self):
        summary = TelemetrySummary(
            sessions_count=10,
            avg_duration_seconds=5.5,
            compactions_count=2,
            errors_count=1,
        )
        assert summary.sessions_count == 10
        assert summary.avg_duration_seconds == 5.5
