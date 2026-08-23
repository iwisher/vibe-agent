"""Tests for AgentHER-style failure relabeling in the RLM dataset export (D2)."""

import json
import logging

from vibe.core.config import RLMConfig
from vibe.memory.rlm_trainer import RLMTrainer

ORIGINAL_GOAL = "Deploy the service to production"
ACHIEVED_OUTCOME = "I could not deploy: port 8080 is already in use. I documented the conflict."


class MockWiki:
    async def list_pages(self, status=None):
        return []


class MixedTraceStore:
    """One successful and one failed session."""

    def get_recent_sessions(self, limit=100):
        return [{"id": "ok1", "success": True}, {"id": "fail1", "success": False}]

    def get_session_trace(self, session_id):
        if session_id == "ok1":
            return {
                "steps": [
                    {"type": "user", "text": "Hello"},
                    {"type": "assistant", "text": "Hi there!"},
                ]
            }
        return {
            "steps": [
                {"type": "user", "text": ORIGINAL_GOAL},
                {"type": "assistant", "text": "Let me check the config first."},
                {"type": "assistant", "text": ACHIEVED_OUTCOME},
            ]
        }


class MockResponse:
    def __init__(self, content):
        self.content = content


class MockLLM:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls: list[str] = []

    async def complete(self, prompt):
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return MockResponse(self.content)


def _trainer(llm=None, relabel_failures=True, min_confidence=0.7):
    config = RLMConfig(relabel_failures=relabel_failures, relabel_min_confidence=min_confidence)
    return RLMTrainer(llm_client=llm, rlm_config=config)


async def _export(trainer, tmp_path):
    out = tmp_path / "dataset.jsonl"
    await trainer.prepare_dataset(MockWiki(), MixedTraceStore(), out)
    text = out.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.strip().split("\n")] if text.strip() else []


class TestRelabelFailures:
    async def test_failed_session_relabeled_with_provenance(self, tmp_path):
        llm = MockLLM(
            json.dumps(
                {
                    "discard": False,
                    "achievable_goal": "Document the port conflict",
                    "confidence": 0.9,
                    "rationale": "the agent diagnosed and documented the blocker",
                }
            )
        )
        records = await _export(_trainer(llm), tmp_path)

        assert len(records) == 2  # successful session + relabeled failure
        relabeled = next(r for r in records if r.get("relabeled"))
        assert relabeled["relabeled"] is True
        assert relabeled["original_goal"] == ORIGINAL_GOAL  # original goal kept
        assert relabeled["relabel_confidence"] == 0.9
        assert relabeled["session_id"] == "fail1"
        assert relabeled["messages"][1] == {
            "role": "user",
            "content": "Document the port conflict",
        }
        assert relabeled["messages"][2] == {"role": "assistant", "content": ACHIEVED_OUTCOME}
        assert len(llm.calls) == 1  # exactly ONE LLM call per failed session

    async def test_low_confidence_discarded(self, tmp_path, caplog):
        llm = MockLLM(json.dumps({"discard": False, "achievable_goal": "g", "confidence": 0.3}))
        with caplog.at_level(logging.INFO, logger="vibe.memory.rlm_trainer"):
            records = await _export(_trainer(llm), tmp_path)
        assert len(records) == 1  # only the successful session
        assert not any(r.get("relabeled") for r in records)
        assert "0 relabeled, 1 discarded" in caplog.text

    async def test_llm_discard_decision(self, tmp_path):
        llm = MockLLM(json.dumps({"discard": True}))
        records = await _export(_trainer(llm), tmp_path)
        assert len(records) == 1

    async def test_relabel_llm_error_excludes_session_silently(self, tmp_path):
        llm = MockLLM(error=ConnectionError("endpoint down"))
        records = await _export(_trainer(llm), tmp_path)  # must not raise
        assert len(records) == 1
        assert not any(r.get("relabeled") for r in records)

    async def test_relabel_unparseable_response_discarded(self, tmp_path):
        llm = MockLLM("I cannot decide about this session.")
        records = await _export(_trainer(llm), tmp_path)
        assert len(records) == 1

    async def test_counts_logged_separately(self, tmp_path, caplog):
        llm = MockLLM(
            json.dumps({"discard": False, "achievable_goal": "Document it", "confidence": 0.95})
        )
        with caplog.at_level(logging.INFO, logger="vibe.memory.rlm_trainer"):
            records = await _export(_trainer(llm), tmp_path)
        assert len(records) == 2
        assert "1 relabeled, 0 discarded" in caplog.text

    async def test_disabled_via_config_byte_identical_export(self, tmp_path):
        llm = MockLLM(json.dumps({"discard": False, "achievable_goal": "g", "confidence": 1.0}))
        disabled = _trainer(llm, relabel_failures=False)
        legacy = RLMTrainer()  # no llm/config → previous behavior

        out_disabled = tmp_path / "disabled.jsonl"
        out_legacy = tmp_path / "legacy.jsonl"
        await disabled.prepare_dataset(MockWiki(), MixedTraceStore(), out_disabled)
        await legacy.prepare_dataset(MockWiki(), MixedTraceStore(), out_legacy)

        assert out_disabled.read_bytes() == out_legacy.read_bytes()
        assert llm.calls == []  # relabeling never invoked

    async def test_no_llm_client_keeps_old_behavior(self, tmp_path):
        # relabel_failures=True in config but no LLM client → stage is inert
        trainer = _trainer(llm=None, relabel_failures=True)
        records = await _export(trainer, tmp_path)
        assert len(records) == 1
