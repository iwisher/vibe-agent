"""Tests for RLM training pipeline — High severity weakness fix.

Tests that RLMThresholdAnalyzer.analyze_and_train() actually triggers
training when auto_train=True, not just logs.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.memory.rlm_analyzer import RLMThresholdAnalyzer
from vibe.memory.rlm_trainer import RLMTrainer


class FakeTelemetry:
    def __init__(self, sessions=None, rows=None):
        self.db = MagicMock()
        self._sessions = sessions or []
        # Mock the DB cursor to return session data
        cursor = MagicMock()
        cursor.fetchall.return_value = rows or [
            (json.dumps({"total_chars": 1000, "duration_seconds": 5.0, "session_id": "s1"}),)
            for _ in range(5)
        ]
        cursor.fetchone.return_value = [0]
        self.db.conn.execute.return_value = cursor

    async def query_sessions(self, limit=50):
        return self._sessions


class FakeConfig:
    trigger_window_sessions = 10
    min_sessions_before_trigger = 1
    trigger_threshold_chars = 100
    trigger_threshold_compaction_pct = 0.1
    auto_train = True
    base_model = "test-model"
    max_train_steps = 10
    lora_r = 4


class FakeWiki:
    async def list_pages(self, status="verified"):
        return []


class FakeTraceStore:
    def get_recent_sessions(self, limit=100):
        return []


@pytest.mark.asyncio
async def test_analyze_and_train_triggers_when_auto_train_enabled():
    """High severity fix: RLM should actually trigger training, not just log."""
    telemetry = FakeTelemetry(
        sessions=[],
        rows=[
            (json.dumps({"total_chars": 1000, "duration_seconds": 5.0, "session_id": "s1"}),)
            for _ in range(5)
        ],
    )
    config = FakeConfig()
    analyzer = RLMThresholdAnalyzer(telemetry, config)

    # Mock the trainer
    trainer = AsyncMock(spec=RLMTrainer)
    trainer.prepare_dataset = AsyncMock(return_value="/tmp/dataset.jsonl")
    trainer.train = AsyncMock(return_value="/tmp/adapter")

    wiki = FakeWiki()
    trace_store = FakeTraceStore()

    decision = await analyzer.analyze_and_train(
        wiki=wiki,
        trace_store=trace_store,
        rlm_trainer=trainer,
        rlm_config=config,
    )

    assert decision.should_trigger is True
    # Wait for background task to complete
    if hasattr(analyzer, "_training_task") and analyzer._training_task:
        await analyzer._training_task
    # Trainer should have been called
    assert trainer.prepare_dataset.called
    assert trainer.train.called


@pytest.mark.asyncio
async def test_analyze_and_train_log_only_when_auto_train_disabled():
    """When auto_train=False, only log but don't train."""
    telemetry = FakeTelemetry(
        sessions=[],
        rows=[
            (json.dumps({"total_chars": 1000, "duration_seconds": 5.0, "session_id": "s1"}),)
            for _ in range(5)
        ],
    )
    config = FakeConfig()
    config.auto_train = False
    analyzer = RLMThresholdAnalyzer(telemetry, config)

    trainer = AsyncMock(spec=RLMTrainer)

    decision = await analyzer.analyze_and_train(
        wiki=FakeWiki(),
        trace_store=FakeTraceStore(),
        rlm_trainer=trainer,
        rlm_config=config,
    )

    assert decision.should_trigger is True
    # Trainer should NOT have been called
    assert not trainer.prepare_dataset.called
    assert not trainer.train.called


@pytest.mark.asyncio
async def test_analyze_and_train_no_trigger_when_below_threshold():
    """When metrics are below threshold, don't trigger at all."""
    telemetry = FakeTelemetry(sessions=[], rows=[])
    config = FakeConfig()
    config.trigger_threshold_chars = 100_000  # Very high so no trigger
    analyzer = RLMThresholdAnalyzer(telemetry, config)

    trainer = AsyncMock(spec=RLMTrainer)

    decision = await analyzer.analyze_and_train(
        wiki=FakeWiki(),
        trace_store=FakeTraceStore(),
        rlm_trainer=trainer,
        rlm_config=config,
    )

    assert decision.should_trigger is False
    assert not trainer.prepare_dataset.called


@pytest.mark.asyncio
async def test_rlm_trainer_prepare_dataset_creates_file(tmp_path):
    """Test dataset preparation creates a valid JSONL file."""
    trainer = RLMTrainer()

    wiki = AsyncMock()
    wiki.list_pages = AsyncMock(return_value=[MagicMock(title="Test", content="Hello world")])

    trace_store = MagicMock()
    trace_store.get_recent_sessions.return_value = []

    dataset_path = tmp_path / "rlm_dataset.jsonl"
    result = await trainer.prepare_dataset(wiki, trace_store, dataset_path)

    assert result == dataset_path
    assert dataset_path.exists()
    content = dataset_path.read_text()
    assert "messages" in content
    assert "Test" in content


@pytest.mark.asyncio
async def test_rlm_trainer_train_subprocess_mock():
    """Test training launches subprocess with correct config."""
    trainer = RLMTrainer()

    from vibe.memory.rlm_trainer import RLMTrainingConfig

    config = RLMTrainingConfig(
        base_model="test-model",
        output_path="/tmp/adapter",
        dataset_path="/tmp/dataset.jsonl",
        max_steps=10,
        lora_r=4,
        ollama_register=False,
    )

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))
        mock_process.returncode = 0
        mock_exec.return_value = mock_process

        result = await trainer.train(config)

        assert result is not None
        mock_exec.assert_called_once()
        # Check it calls the worker module
        args = mock_exec.call_args[0]
        assert "vibe.memory._rlm_train_worker" in args
