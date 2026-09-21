"""Fast Veto Gate for SmartApprover.

Provides a low-latency System 1 pre-filter using direct-logit readout
over candidate tokens (or constrained JSON fallback) on a local model
(e.g., Qwen3.5 4B via llama-server or Ollama).

Strict Invariant: VETO-ONLY.
The gate can only early-reject high-confidence dangerous calls or escalate to
the primary LLM/human approval pipeline. It never grants auto-approval.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

import httpx

from vibe.tools.security.smart_approver import (
    UNTRUSTED_ARGS_BEGIN,
    UNTRUSTED_ARGS_END,
    UNTRUSTED_CONTEXT_BEGIN,
    UNTRUSTED_CONTEXT_END,
    RiskLevel,
    munge_fence_markers,
    strip_json_fence,
)

logger = logging.getLogger(__name__)

GATE_MODES = ("auto", "logit", "json")
GATE_TRANSPORTS = ("http", "mlx")

# Single-token candidates for the logit readout: A = safe, B = dangerous.
_CANDIDATES = ("A", "B")

_PROBE_PROMPT = "Reply with the single letter A."


def _two_way_softmax_b(logit_a: float, logit_b: float) -> float:
    """Numerically stable conditional P(B) over the two candidate slots.

    Conditional score only — mass on answers outside {A, B} is excluded, so
    this is not calibrated confidence (see plan §1 / F1).
    """
    max_l = max(logit_a, logit_b)
    exp_a = math.exp(logit_a - max_l)
    exp_b = math.exp(logit_b - max_l)
    return exp_b / (exp_a + exp_b)


@dataclass
class GateVerdict:
    """Verdict returned by the fast veto gate.

    Attributes:
        reject: True if the tool call is judged dangerous with high confidence.
        risk_level: RiskLevel if rejected, or None.
        confidence: Normalized probability or confidence score (0.0 to 1.0).
        reasoning: Brief diagnostic explanation.
    """

    reject: bool = False
    risk_level: RiskLevel | None = None
    confidence: float = 0.0
    reasoning: str = ""


class FastGateClient:
    """Synchronous, connection-pooled client for the fast veto gate.

    Modes (HTTP transport only):
    - "logit": single-token direct logit readout over candidate tokens,
      normalized softmax P(Dangerous). Requires server-side logprobs support
      (e.g. llama-server; Ollama's OpenAI layer does not reliably return them).
    - "json": constrained JSON verdict fallback.
    - "auto" (default): probe the endpoint once for logprobs support, then use
      logit when available and json otherwise. Probe failures are not cached.

    Transports:
    - "http" (default): OpenAI-compatible endpoint (llama-server, Ollama, omlx).
    - "mlx": in-process direct readout via mlx-lm (Apple Silicon) — true
      0-token prefill readout with full-vocabulary access, so the
      candidate-missing guard does not apply. `mode` is ignored.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen3.5:4b",
        timeout: float = 0.5,
        reject_confidence: float = 0.85,
        mode: str = "auto",
        client: httpx.Client | None = None,
        transport: str = "http",
        model_revision: str = "",
        quantize_bits: int | None = None,
        max_prompt_tokens: int = 4096,
        mlx_backend: Any | None = None,
    ) -> None:
        if mode not in GATE_MODES:
            raise ValueError(f"unknown fast-gate mode {mode!r}; expected one of {GATE_MODES}")
        if transport not in GATE_TRANSPORTS:
            raise ValueError(
                f"unknown fast-gate transport {transport!r}; expected one of {GATE_TRANSPORTS}"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.reject_confidence = reject_confidence
        self.mode = mode
        self.transport = transport
        self._resolved_mode: str | None = None if mode == "auto" else mode
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._available: bool | None = None
        self._mlx_backend = mlx_backend
        if self.transport == "mlx" and self._mlx_backend is None:
            from vibe.tools.security.fast_gate_mlx import MlxGateBackend

            self._mlx_backend = MlxGateBackend(
                model_path=model,
                revision=model_revision,
                quantize_bits=quantize_bits,
                max_prompt_tokens=max_prompt_tokens,
            )

    def close(self) -> None:
        """Close the underlying HTTP client if owned, and any MLX backend."""
        if self._owns_client and self._client is not None:
            self._client.close()
        if self._mlx_backend is not None:
            try:
                self._mlx_backend.close()
            except Exception:  # noqa: BLE001 - teardown must never raise
                logger.debug("MlxGateBackend.close failed", exc_info=True)

    def available(self) -> bool:
        """Check if the gate is usable (cached on success)."""
        if self.transport == "mlx":
            return bool(self._mlx_backend and self._mlx_backend.available())
        if self._available is not None:
            return self._available
        try:
            url = f"{self.base_url}/models"
            resp = self._client.get(url, timeout=min(self.timeout, 1.0))
            self._available = resp.is_success
        except Exception as e:
            logger.debug("FastGateClient unavailable: %s", e)
            self._available = False
        return self._available

    def check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: str | None = None,
    ) -> GateVerdict:
        """Check a tool call against the fast gate.

        Strictly veto-only: returns reject=True only if risk is detected with
        confidence >= reject_confidence. All failures and benign calls return
        reject=False (escalate).
        """
        if not self.available():
            return GateVerdict(reject=False, reasoning="gate endpoint unavailable")
        try:
            if self.transport == "mlx":
                return self._check_mlx(tool_name, tool_args, context)
            if self._resolve_mode() == "logit":
                return self._check_logit(tool_name, tool_args, context)
            return self._check_json(tool_name, tool_args, context)
        except Exception as exc:
            logger.debug("FastGateClient.check failed (escalating to LLM): %s", exc)
            return GateVerdict(reject=False, reasoning=f"gate_error: {exc}")

    def _resolve_mode(self) -> str:
        """Resolve "auto" mode by probing the endpoint for logprobs support.

        Successful probes are cached; failures propagate (check() escalates)
        and are retried on the next call.
        """
        if self._resolved_mode is not None:
            return self._resolved_mode
        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": _PROBE_PROMPT}],
                "max_tokens": 1,
                "logprobs": True,
                "top_logprobs": 5,
                "temperature": 0.0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        has_logprobs = bool((data.get("choices", [{}])[0].get("logprobs") or {}).get("content"))
        self._resolved_mode = "logit" if has_logprobs else "json"
        logger.info(
            "FastGateClient resolved mode=%r for %s (logprobs %s)",
            self._resolved_mode,
            self.base_url,
            "supported" if has_logprobs else "absent",
        )
        return self._resolved_mode

    def _build_prompt(
        self, tool_name: str, tool_args: dict[str, Any], context: str | None = None
    ) -> str:
        fenced_args = munge_fence_markers(json.dumps(tool_args, indent=2))
        fenced_ctx = munge_fence_markers(context or "No additional context")
        return (
            f"Assess the security risk of this tool call:\n"
            f"Tool: {tool_name}\n"
            f"{UNTRUSTED_ARGS_BEGIN}\n{fenced_args}\n{UNTRUSTED_ARGS_END}\n"
            f"{UNTRUSTED_CONTEXT_BEGIN}\n{fenced_ctx}\n{UNTRUSTED_CONTEXT_END}\n"
        )

    def _build_logit_prompt(
        self, tool_name: str, tool_args: dict[str, Any], context: str | None = None
    ) -> str:
        base_prompt = self._build_prompt(tool_name, tool_args, context)
        return (
            f"{base_prompt}\n"
            f"Does this tool call pose a security risk (e.g. destructive commands, "
            f"privilege escalation, unauthorized file access)?\n"
            f"A: Safe / Benign\n"
            f"B: Dangerous / Risky\n\n"
            f"Answer with a single letter (A or B):"
        )

    def _check_mlx(
        self, tool_name: str, tool_args: dict[str, Any], context: str | None = None
    ) -> GateVerdict:
        """In-process direct readout via the MLX backend (no HTTP round-trip)."""
        prompt = self._build_logit_prompt(tool_name, tool_args, context)
        readout = self._mlx_backend.readout(prompt)
        if not readout.ok:
            return GateVerdict(reject=False, reasoning=f"mlx readout: {readout.reason}")

        p_dangerous = _two_way_softmax_b(readout.logit_a, readout.logit_b)
        if p_dangerous >= self.reject_confidence:
            return GateVerdict(
                reject=True,
                risk_level=RiskLevel.CRITICAL,
                confidence=round(p_dangerous, 4),
                reasoning=(
                    f"Fast veto (mlx): P(Dangerous)={p_dangerous:.2f} >= {self.reject_confidence}"
                ),
            )
        return GateVerdict(
            reject=False,
            confidence=round(p_dangerous, 4),
            reasoning=(
                f"Fast pass to LLM (mlx): P(Dangerous)={p_dangerous:.2f} < {self.reject_confidence}"
            ),
        )

    def _check_logit(
        self, tool_name: str, tool_args: dict[str, Any], context: str | None = None
    ) -> GateVerdict:
        prompt = self._build_logit_prompt(tool_name, tool_args, context)
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1,
            "logprobs": True,
            "top_logprobs": 5,
            "temperature": 0.0,
            # Reasoning models (e.g. Qwen3.5) must not open with <think>;
            # servers that ignore this kwarg are unaffected.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data.get("choices", [{}])[0]
        logprobs_content = (choice.get("logprobs") or {}).get("content", [])
        if not logprobs_content:
            # The server ignored logprobs (e.g. Ollama's OpenAI layer). A bare
            # text answer carries no confidence signal — escalate.
            return GateVerdict(reject=False, reasoning="no logprobs in response")

        first = logprobs_content[0]
        emitted = first.get("token", "").strip().upper()
        if emitted not in _CANDIDATES:
            # Argmax outside {A, B} (e.g. <think>) — the readout is unreadable.
            return GateVerdict(
                reject=False, reasoning=f"argmax token {emitted!r} outside candidates"
            )

        logits: dict[str, float] = {}
        for entry in first.get("top_logprobs", []):
            token = entry.get("token", "").strip().upper()
            lp = entry.get("logprob")
            if token in _CANDIDATES and lp is not None:
                logits[token] = lp

        if "A" not in logits or "B" not in logits:
            # Absence of a candidate from top_logprobs is not evidence; the
            # two-way softmax would manufacture confidence from missing mass.
            return GateVerdict(reject=False, reasoning="candidate missing from top_logprobs")

        p_dangerous = _two_way_softmax_b(logits["A"], logits["B"])

        if p_dangerous >= self.reject_confidence:
            return GateVerdict(
                reject=True,
                risk_level=RiskLevel.CRITICAL,
                confidence=round(p_dangerous, 4),
                reasoning=(
                    f"Fast veto: P(Dangerous)={p_dangerous:.2f} >= {self.reject_confidence}"
                ),
            )
        return GateVerdict(
            reject=False,
            confidence=round(p_dangerous, 4),
            reasoning=(
                f"Fast pass to LLM: P(Dangerous)={p_dangerous:.2f} < {self.reject_confidence}"
            ),
        )

    def _check_json(
        self, tool_name: str, tool_args: dict[str, Any], context: str | None = None
    ) -> GateVerdict:
        base_prompt = self._build_prompt(tool_name, tool_args, context)
        prompt = (
            f"{base_prompt}\n"
            f"Rate if this tool call is dangerous. Respond in JSON format:\n"
            f'{{"dangerous": true, "risk": "critical", '
            f'"confidence": 0.95, "reasoning": "brief explanation"}}'
        )
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        clean_json = strip_json_fence(raw_content)
        parsed = json.loads(clean_json)

        dangerous = bool(parsed.get("dangerous", False))
        confidence = float(parsed.get("confidence", 0.0))
        risk_str = str(parsed.get("risk", "high")).lower()
        reasoning = str(parsed.get("reasoning", "JSON fast gate assessment"))

        try:
            risk_level = RiskLevel(risk_str)
        except ValueError:
            risk_level = RiskLevel.HIGH

        if dangerous and confidence >= self.reject_confidence:
            return GateVerdict(
                reject=True,
                risk_level=risk_level,
                confidence=confidence,
                reasoning=f"Fast veto (JSON): {reasoning}",
            )
        return GateVerdict(
            reject=False,
            confidence=confidence,
            reasoning="Fast pass to LLM (JSON)",
        )
