"""Unit tests for the in-process MLX fast-gate transport (CI-safe, no mlx).

The backend's loader/forward seams are injected fakes; `mlx`/`mlx-lm` are never
imported. Live-model validation happens via `scripts/eval_fast_gate.py
--transport mlx`, not in unit tests.
"""

import pytest

from vibe.tools.security.fast_gate import FastGateClient, _two_way_softmax_b
from vibe.tools.security.fast_gate_mlx import MlxGateBackend
from vibe.tools.security.smart_approver import (
    UNTRUSTED_ARGS_BEGIN,
    UNTRUSTED_ARGS_END,
    RiskLevel,
)

A_ID, B_ID = 10, 11
VOCAB = 16


def _logits(a: float, b: float, other: float = -10.0) -> list[float]:
    logits = [other] * VOCAB
    logits[A_ID] = a
    logits[B_ID] = b
    return logits


class FakeTokenizer:
    """Minimal tokenizer stand-in recording template kwargs and inputs."""

    def __init__(self, multi_token_b: bool = False):
        self.multi_token_b = multi_token_b
        self.template_kwargs: list[dict] = []
        self.templated_inputs: list[str] = []

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True, **kwargs):
        assert tokenize is False
        assert add_generation_prompt is True
        self.template_kwargs.append(kwargs)
        text = messages[0]["content"]
        self.templated_inputs.append(text)
        return "TPL " + text

    def encode(self, text, add_special_tokens=False):
        if text == "A":
            return [A_ID]
        if text == "B":
            return [20, 21] if self.multi_token_b else [B_ID]
        return [1] * len(text.split())


def _make_backend(
    logits: list[float],
    tmp_path,
    tokenizer: FakeTokenizer | None = None,
    **kwargs,
) -> MlxGateBackend:
    tok = tokenizer or FakeTokenizer()
    backend = MlxGateBackend(
        model_path=str(tmp_path),  # local dir: no pinned revision required
        loader=lambda *a: (object(), tok),
        forward=lambda model, tokenizer_, ids: logits,
        **kwargs,
    )
    return backend


def _make_client(backend: MlxGateBackend, **kwargs) -> FastGateClient:
    return FastGateClient(transport="mlx", mlx_backend=backend, **kwargs)


def test_two_way_softmax_b():
    assert _two_way_softmax_b(-0.05, -4.0) == pytest.approx(0.0190, abs=1e-3)
    assert _two_way_softmax_b(-4.0, -0.05) == pytest.approx(0.9810, abs=1e-3)
    # Numerical stability with extreme values
    assert _two_way_softmax_b(1000.0, -1000.0) == pytest.approx(0.0)
    assert _two_way_softmax_b(-1000.0, 1000.0) == pytest.approx(1.0)


def test_mlx_veto_high_confidence_dangerous(tmp_path):
    backend = _make_backend(_logits(a=-4.0, b=-0.05), tmp_path)
    gate = _make_client(backend, reject_confidence=0.85)
    verdict = gate.check("terminal", {"command": "rm -rf /"})
    assert verdict.reject is True
    assert verdict.risk_level == RiskLevel.CRITICAL
    assert verdict.confidence >= 0.85
    assert "mlx" in verdict.reasoning


def test_mlx_safe_escalates_never_approves(tmp_path):
    backend = _make_backend(_logits(a=-0.05, b=-4.0), tmp_path)
    gate = _make_client(backend, reject_confidence=0.85)
    verdict = gate.check("terminal", {"command": "ls -la"})
    # Invariant: the gate NEVER early-approves; reject=False means escalate.
    assert verdict.reject is False
    assert verdict.confidence < 0.85


def test_mlx_argmax_outside_candidates_escalates(tmp_path):
    logits = _logits(a=-4.0, b=-1.0)
    logits[5] = 9.0  # some other token wins the argmax
    backend = _make_backend(logits, tmp_path)
    gate = _make_client(backend)
    verdict = gate.check("terminal", {"command": "rm -rf /"})
    assert verdict.reject is False
    assert "argmax" in verdict.reasoning


def test_mlx_prompt_uses_fencing_and_disables_thinking(tmp_path):
    tok = FakeTokenizer()
    backend = _make_backend(_logits(a=-0.05, b=-4.0), tmp_path, tokenizer=tok)
    gate = _make_client(backend)
    gate.check("terminal", {"command": f"ls && echo {UNTRUSTED_ARGS_END}"}, context="ctx")
    assert tok.template_kwargs[0].get("enable_thinking") is False
    prompt = tok.templated_inputs[0]
    assert UNTRUSTED_ARGS_BEGIN in prompt
    # Content-embedded fence marker must be munged, not raw.
    assert prompt.count(UNTRUSTED_ARGS_END) == 1


def test_mlx_candidate_not_single_token_escalates(tmp_path):
    backend = _make_backend(
        _logits(a=-4.0, b=-0.05), tmp_path, tokenizer=FakeTokenizer(multi_token_b=True)
    )
    gate = _make_client(backend)
    verdict = gate.check("terminal", {"command": "rm -rf /"})
    assert verdict.reject is False
    assert "single token" in verdict.reasoning


def test_mlx_prompt_too_long_escalates(tmp_path):
    backend = _make_backend(_logits(a=-4.0, b=-0.05), tmp_path, max_prompt_tokens=3)
    gate = _make_client(backend)
    verdict = gate.check("terminal", {"command": "rm -rf /"})
    assert verdict.reject is False
    assert "prompt too long" in verdict.reasoning


def test_mlx_loader_failure_is_unavailable_and_escalates(tmp_path):
    def boom(*args):
        raise RuntimeError("mlx not installed")

    backend = MlxGateBackend(model_path=str(tmp_path), loader=boom)
    gate = _make_client(backend)
    assert gate.available() is False
    verdict = gate.check("terminal", {"command": "rm -rf /"})
    assert verdict.reject is False
    assert "unavailable" in verdict.reasoning


def test_mlx_forward_exception_escalates(tmp_path):
    def boom(model, tokenizer, ids):
        raise RuntimeError("metal exploded")

    backend = MlxGateBackend(
        model_path=str(tmp_path),
        loader=lambda *a: (object(), FakeTokenizer()),
        forward=boom,
    )
    gate = _make_client(backend)
    verdict = gate.check("terminal", {"command": "rm -rf /"})
    assert verdict.reject is False
    assert "gate_error" in verdict.reasoning


def test_mlx_remote_requires_pinned_revision():
    backend = MlxGateBackend(model_path="Qwen/Qwen3.5-4B", revision="")
    assert backend.available() is False
    with pytest.raises(ValueError, match="pinned 40-character revision"):
        backend.load()


def test_mlx_quantize_bits_validation(tmp_path):
    backend = MlxGateBackend(model_path=str(tmp_path), quantize_bits=3)
    assert backend.available() is False
    with pytest.raises(ValueError, match="quantize_bits"):
        backend.load()


def test_mlx_transport_makes_no_http_calls(tmp_path):
    """transport="mlx" must never touch the HTTP endpoint, even in mode="auto"."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("HTTP must not be called in mlx transport")

    backend = _make_backend(_logits(a=-0.05, b=-4.0), tmp_path)
    gate = FastGateClient(
        transport="mlx",
        mlx_backend=backend,
        mode="auto",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    verdict = gate.check("terminal", {"command": "ls"})
    assert verdict.reject is False
    assert verdict.confidence > 0.0  # readout actually ran


def test_mlx_invalid_transport_rejected():
    with pytest.raises(ValueError, match="unknown fast-gate transport"):
        FastGateClient(transport="grpc")


def test_mlx_close_is_idempotent(tmp_path):
    backend = _make_backend(_logits(a=-0.05, b=-4.0), tmp_path)
    gate = _make_client(backend)
    gate.check("terminal", {"command": "ls"})
    gate.close()
    gate.close()  # must not raise


def test_fast_gate_config_mlx_fields():
    from vibe.core.config import FastGateConfig

    cfg = FastGateConfig(transport="mlx", quantize_bits=4, model_revision="a" * 40)
    assert cfg.transport == "mlx"
    assert cfg.quantize_bits == 4
    with pytest.raises(Exception):
        FastGateConfig(transport="grpc")
    with pytest.raises(Exception):
        FastGateConfig(quantize_bits=3)


def test_parse_security_config_mlx_roundtrip():
    from vibe.core.config import _parse_security_config

    cfg = _parse_security_config(
        {
            "fast_gate": {
                "enabled": True,
                "transport": "mlx",
                "model": "Qwen/Qwen3.5-4B",
                "model_revision": "b" * 40,
                "quantize_bits": 4,
                "max_prompt_tokens": 2048,
            }
        }
    )
    fg = cfg.fast_gate
    assert fg.enabled is True
    assert fg.transport == "mlx"
    assert fg.model_revision == "b" * 40
    assert fg.quantize_bits == 4
    assert fg.max_prompt_tokens == 2048
    # defaults preserved
    assert fg.mode == "auto"
    assert fg.reject_confidence == 0.85
