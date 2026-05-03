"""Unit tests for the Phase 3b RLM Training Pipeline."""

import json
from pathlib import Path

import pytest

from vibe.memory.models import WikiPage
from vibe.memory.rlm_trainer import RLMTrainer, RLMTrainingConfig


class MockWiki:
    async def list_pages(self, status=None):
        return [
            WikiPage(
                id="123",
                slug="test-page",
                title="Test Page",
                content="This is test content.",
                tags=["test"],
                status="verified",
                date_created="2023-01-01",
                last_updated="2023-01-01",
                citations=[],
                ttl_days=30,
                path=Path("/tmp/wiki/123.md")
            )
        ]


class MockTraceStore:
    def get_recent_sessions(self, limit=100):
        return [{"id": "s1", "success": True}, {"id": "s2", "success": False}]

    def get_session_trace(self, session_id):
        if session_id == "s1":
            return {
                "steps": [
                    {"type": "user", "text": "Hello"},
                    {"type": "assistant", "text": "Hi there!"}
                ]
            }
        return None


@pytest.fixture
def trainer():
    return RLMTrainer()


@pytest.mark.asyncio
async def test_prepare_dataset(trainer, tmp_path):
    output_path = tmp_path / "dataset.jsonl"
    await trainer.prepare_dataset(MockWiki(), MockTraceStore(), output_path)

    assert output_path.exists()
    lines = output_path.read_text().strip().split("\n")
    assert len(lines) == 2

    record1 = json.loads(lines[0])
    assert record1["messages"][1]["content"] == "Tell me about Test Page."

    record2 = json.loads(lines[1])
    assert record2["messages"][1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_train_subprocess_mocked(trainer, tmp_path, monkeypatch):
    """Test that trainer.train invokes subprocess correctly."""

    config = RLMTrainingConfig(
        base_model="qwen3:1.7b",
        output_path=str(tmp_path / "output"),
        dataset_path=str(tmp_path / "dataset.jsonl"),
        hf_model_id="Qwen/Qwen1.5-1.8B-Chat",
        ollama_register=False
    )

    class MockProcess:
        returncode = 0
        async def communicate(self, input):
            return b"stdout", b"stderr"

    async def mock_create_subprocess_exec(*args, **kwargs):
        # Verify args
        assert args[0].endswith("python") or "python" in args[0]
        assert "vibe.memory._rlm_train_worker" in args
        return MockProcess()

    import asyncio
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)

    result = await trainer.train(config)
    assert result == Path(config.output_path)


@pytest.mark.asyncio
async def test_register_with_ollama(trainer, monkeypatch):
    class MockResponse:
        def raise_for_status(self):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json):
            assert "api/create" in url
            assert json["name"] == "test-model-rlm"
            assert "ADAPTER /tmp/adapter" in json["modelfile"]
            return MockResponse()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    success = await trainer.register_with_ollama("/tmp/adapter", "test-model-rlm")
    assert success is True
