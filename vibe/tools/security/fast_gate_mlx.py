"""In-process MLX backend for the fast veto gate (Apple Silicon).

SemIf-style direct readout without an HTTP server: one forward pass over the
fenced gate prompt, last-position logits gathered at the candidate-token slots,
two-way conditional softmax. Scores remain conditional on the declared
candidates, not calibrated confidence.

Adapted from the SemIf reference implementation (TheoLeeCJ/SemIf, MIT),
`src/semif_phase1/mlx_backend.py` — the readout core only. Prompt construction
and fencing stay in `fast_gate.py`; this module owns model lifecycle and the
forward pass.

`mlx`/`mlx-lm` are optional dependencies: nothing here imports them at module
level, and every failure surfaces as "unavailable / escalate" (fail-open), never
as an exception into the approval path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_REMOTE_REVISION_RE = re.compile(r"[0-9a-f]{40}")

# Candidate answer tokens for the binary risk readout (A = safe, B = dangerous).
CANDIDATE_TOKENS = ("A", "B")


@dataclass
class MlxReadout:
    """Result of one in-process readout.

    Attributes:
        ok: True when logits for both candidates were read and the argmax
            token is one of the candidates. False means "escalate".
        reason: Diagnostic detail (empty on success).
        logit_a / logit_b: Last-position logits at the candidate token slots.
        input_tokens: Prompt length in tokens (for latency/memory profiling).
    """

    ok: bool
    reason: str = ""
    logit_a: float = 0.0
    logit_b: float = 0.0
    input_tokens: int = 0


class MlxGateBackend:
    """Loads a local model via mlx-lm and scores prompts in-process.

    Parameters:
        model_path: Hugging Face repo id (e.g. "Qwen/Qwen3.5-4B") or a local
            directory with an MLX-format checkpoint.
        revision: Pinned 40-character commit SHA — required for remote models
            (provenance, following SemIf's loader); a label is sufficient for
            local directories.
        quantize_bits: Optional in-memory affine quantization (4 or 8) applied
            to an unquantized checkpoint.
        max_prompt_tokens: Prompts longer than this escalate instead of
            running (bounds memory/time for an in-process forward pass).
        loader / forward: Test seams. Defaults lazily import mlx-lm / mlx.
    """

    def __init__(
        self,
        model_path: str,
        revision: str = "",
        quantize_bits: int | None = None,
        max_prompt_tokens: int = 4096,
        loader: Callable[[str, str, int | None], tuple[Any, Any]] | None = None,
        forward: Callable[[Any, Any, list[int]], list[float]] | None = None,
    ) -> None:
        self.model_path = model_path
        self.revision = revision
        self.quantize_bits = quantize_bits
        self.max_prompt_tokens = max_prompt_tokens
        self._loader = loader or self._default_loader
        self._forward = forward or self._default_forward
        self._model: Any = None
        self._tokenizer: Any = None
        self._available: bool | None = None

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def _default_loader(model_path: str, revision: str, bits: int | None) -> tuple[Any, Any]:
        try:
            import mlx.core as mx
            import mlx.nn as nn
            from mlx_lm import load
        except ImportError as exc:
            raise RuntimeError(
                "MLX transport requires the optional mlx dependencies: pip install mlx mlx-lm"
            ) from exc
        if not mx.metal.is_available():
            raise RuntimeError("MLX Metal GPU is unavailable")

        path = Path(model_path)
        if not path.is_dir():
            from huggingface_hub import snapshot_download

            path = Path(
                snapshot_download(
                    model_path,
                    revision=revision,
                    allow_patterns=["*.json", "model*.safetensors", "*.jinja", "*.txt", "*.model"],
                )
            )
        model, tokenizer = load(str(path), tokenizer_config={"trust_remote_code": False})
        if bits:
            nn.quantize(model, group_size=64, bits=bits, mode="affine")
        model.eval()
        mx.eval(model.parameters())
        mx.synchronize()
        return model, tokenizer

    def _validate_source(self) -> None:
        if self.quantize_bits not in (None, 4, 8):
            raise ValueError("quantize_bits must be 4, 8, or None")
        if Path(self.model_path).is_dir():
            return
        if not _REMOTE_REVISION_RE.fullmatch(self.revision or ""):
            raise ValueError(
                "remote MLX models require a pinned 40-character revision "
                f"(got {self.revision!r} for {self.model_path!r})"
            )

    def load(self) -> None:
        if self._model is not None:
            return
        self._validate_source()
        self._model, self._tokenizer = self._loader(
            self.model_path, self.revision, self.quantize_bits
        )
        logger.info(
            "MlxGateBackend loaded %s (quantize_bits=%s)", self.model_path, self.quantize_bits
        )

    def available(self) -> bool:
        """True when the model can be loaded; cached on success, retried on failure."""
        if self._available is not None:
            return self._available
        try:
            self.load()
            self._available = True
        except Exception as exc:
            logger.debug("MlxGateBackend unavailable: %s", exc)
            self._available = False
        return self._available

    def close(self) -> None:
        """Drop model references and best-effort release the MLX Metal cache."""
        self._model = None
        self._tokenizer = None
        self._available = None
        try:
            import mlx.core as mx

            mx.metal.clear_cache()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass

    # -- readout -----------------------------------------------------------

    def _default_forward(self, model: Any, tokenizer: Any, ids: list[int]) -> list[float]:
        import mlx.core as mx

        out = model(mx.array([ids]))
        logits = getattr(out, "logits", out)
        last = logits[0, -1].astype(mx.float32)
        mx.eval(last)
        return last.tolist()

    def _encode_prompt(self, prompt: str) -> list[int]:
        templated = self._tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
            # Reasoning models (e.g. Qwen3.5) must not open with <think>;
            # the first generated position must be the answer letter.
            enable_thinking=False,
        )
        return self._tokenizer.encode(templated, add_special_tokens=False)

    def _candidate_slots(self) -> tuple[int, int]:
        slots = []
        for token in CANDIDATE_TOKENS:
            ids = self._tokenizer.encode(token, add_special_tokens=False)
            if len(ids) != 1:
                raise ValueError(f"candidate token {token!r} is not a single token: {ids}")
            slots.append(ids[0])
        return slots[0], slots[1]

    def readout(self, prompt: str) -> MlxReadout:
        """Score a prompt; any guard failure returns ok=False (escalate)."""
        self.load()
        ids = self._encode_prompt(prompt)
        if len(ids) > self.max_prompt_tokens:
            return MlxReadout(
                ok=False,
                reason=f"prompt too long: {len(ids)} > {self.max_prompt_tokens} tokens",
                input_tokens=len(ids),
            )
        try:
            slot_a, slot_b = self._candidate_slots()
        except ValueError as exc:
            return MlxReadout(ok=False, reason=str(exc), input_tokens=len(ids))

        logits = self._forward(self._model, self._tokenizer, ids)
        if not isinstance(logits, list) or slot_b >= len(logits):
            size = len(logits) if isinstance(logits, list) else "n/a"
            return MlxReadout(
                ok=False,
                reason=f"malformed logits (len={size})",
                input_tokens=len(ids),
            )

        argmax_id = max(range(len(logits)), key=logits.__getitem__)
        if argmax_id not in (slot_a, slot_b):
            # The model's first choice is neither candidate (e.g. <think>);
            # the two-way softmax would hide that "neither" mass.
            return MlxReadout(
                ok=False,
                reason=f"argmax token id {argmax_id} outside candidate slots {slot_a}/{slot_b}",
                input_tokens=len(ids),
            )
        return MlxReadout(
            ok=True,
            logit_a=logits[slot_a],
            logit_b=logits[slot_b],
            input_tokens=len(ids),
        )
