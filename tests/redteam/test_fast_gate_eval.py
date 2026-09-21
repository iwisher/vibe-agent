"""Tests for the standalone FastGate evaluation runner."""

import json
from pathlib import Path

import httpx
import pytest

from scripts.eval_fast_gate import run_fast_gate_eval
from vibe.tools.security.fast_gate import FastGateClient

CORPUS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "vibe"
    / "redteam"
    / "corpus"
    / "s4_approval.yaml"
)


def test_eval_fast_gate_with_mock_transport():
    """Verify run_fast_gate_eval accurately computes metrics with a mock client."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "B"},
                        "logprobs": {
                            "content": [
                                {
                                    "token": "B",
                                    "top_logprobs": [
                                        {"token": "A", "logprob": -5.0},
                                        {"token": "B", "logprob": -0.01},
                                    ],
                                }
                            ]
                        },
                    }
                ]
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    gate = FastGateClient(client=mock_client, mode="logit")

    summary = run_fast_gate_eval(CORPUS_PATH, gate_client=gate)
    assert summary["total_entries"] >= 4
    assert summary["hostile"]["total"] >= 3
    assert summary["hostile"]["catch_rate_gated"] >= summary["hostile"]["catch_rate_baseline"]
    assert "p50" in summary["latency_ms"]
    assert "p95" in summary["latency_ms"]
    assert "p50" in summary["gate_latency_ms"]
    assert "p95" in summary["gate_latency_ms"]


def _write_semif_rows(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "semif_results.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_export_semif_decisions(tmp_path):
    """Corpus exports to SemIf's decisions schema with binary options."""
    from scripts.eval_fast_gate import export_semif_decisions

    out = tmp_path / "decisions.jsonl"
    count = export_semif_decisions(CORPUS_PATH, out)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert count == len(rows) >= 4
    row = rows[0]
    assert set(row) == {"id", "state", "question", "options"}
    assert [o["id"] for o in row["options"]] == ["safe", "dangerous"]
    assert "Tool:" in row["state"]


def test_semif_agreement_metric():
    """Crosscheck math: binary agreement + mean |ΔP| over comparable ids only."""
    from scripts.eval_fast_gate import _semif_agreement

    gate_entries = [
        {"id": "e1", "gate_confidence": 0.98},
        {"id": "e2", "gate_confidence": 0.10},
        {"id": "e3", "gate_confidence": 0.90},
        {"id": "no-gate-verdict"},  # gate not consulted (heuristics preempted)
        {"id": "missing-in-semif", "gate_confidence": 0.5},
    ]
    semif_rows = [
        {"id": "e1", "option_ids": ["safe", "dangerous"], "probabilities": [0.04, 0.96]},
        {"id": "e2", "option_ids": ["safe", "dangerous"], "probabilities": [0.85, 0.15]},
        {"id": "e3", "option_ids": ["safe", "dangerous"], "probabilities": [0.60, 0.40]},
    ]
    result = _semif_agreement(gate_entries, semif_rows)
    assert result["compared"] == 3
    # e1 agree (both dangerous), e2 agree (both safe), e3 disagree
    assert result["argmax_agreement"] == pytest.approx(2 / 3, abs=1e-4)
    # deltas: 0.02, 0.05, 0.50
    assert result["mean_abs_delta_p"] == pytest.approx((0.02 + 0.05 + 0.50) / 3, abs=1e-4)


def test_semif_agreement_empty_when_no_overlap():
    from scripts.eval_fast_gate import _semif_agreement

    result = _semif_agreement([{"id": "x", "gate_confidence": 0.9}], [])
    assert result["compared"] == 0
    assert result["argmax_agreement"] is None


def test_eval_with_mlx_transport_gate():
    """run_fast_gate_eval drives an mlx-transport gate and records confidence."""
    from vibe.tools.security.smart_approver import RiskLevel

    class FakeVerdict:
        def __init__(self, reject, confidence):
            self.reject = reject
            self.risk_level = RiskLevel.CRITICAL if reject else None
            self.confidence = confidence
            self.reasoning = "fake mlx"

    class FakeMlxGate:
        transport = "mlx"
        model = "fake-qwen"
        mode = "auto"

        def available(self):
            return True

        def check(self, tool_name, tool_args, context=None):
            dangerous = "rm -rf" in str(tool_args) or "curl" in str(tool_args)
            return FakeVerdict(dangerous, 0.97 if dangerous else 0.03)

    summary = run_fast_gate_eval(CORPUS_PATH, gate_client=FakeMlxGate())
    assert summary["transport"] == "mlx"
    assert summary["hostile"]["catch_rate_gated"] >= summary["hostile"]["catch_rate_baseline"]
    with_confidence = [e for e in summary["entries"] if "gate_confidence" in e]
    assert with_confidence, "gate confidence must be recorded per entry"
    assert all(0.0 <= e["gate_confidence"] <= 1.0 for e in with_confidence)


def test_eval_gate_veto_actually_lifts_catch_rate():
    """A gate whose vetoes propagate must raise the gated catch rate on the
    40-entry calibration corpus above the heuristic-only baseline."""
    from vibe.tools.security.smart_approver import RiskLevel

    GATE_CORPUS = CORPUS_PATH.parent.parent / "corpus_gate" / "gate_calibration.yaml"

    class VetoVerdict:
        def __init__(self, reject, confidence):
            self.reject = reject
            self.risk_level = RiskLevel.CRITICAL if reject else None
            self.confidence = confidence
            self.reasoning = "stub"

    class VetoAllGate:
        transport = "mlx"
        model = "stub"
        mode = "auto"

        def available(self):
            return True

        def check(self, tool_name, tool_args, context=None):
            return VetoVerdict(True, 0.99)

    summary = run_fast_gate_eval(GATE_CORPUS, gate_client=VetoAllGate())
    # Veto-everything is maximally hostile-catching (and maximally false-positive);
    # this verifies verdict propagation, not gate quality.
    assert summary["hostile"]["catch_rate_gated"] == 1.0
    assert summary["benign"]["fpr_gated"] == 1.0
    assert summary["hostile"]["catch_rate_gated"] > summary["hostile"]["catch_rate_baseline"]
