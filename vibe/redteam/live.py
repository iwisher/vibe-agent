"""Live-tier (Tier C) target configuration.

vibe-agent reaches every provider through the OpenAI-compatible adapter with a
custom ``base_url``. This module centralizes that wiring so Tier C runs against
a managed endpoint without hardcoding URLs in the runner.

Credential scoping rule: only endpoint-specific env vars are honored — a
generic key (``VIBE_API_KEY``) may belong to a different provider and must
never be sent to the wrong endpoint. Callers must treat an empty ``api_key``
as "skip" and never construct a client from it (``LLMClient`` falls back to a
generic ``LLM_API_KEY`` env var when given an empty key).
"""

import os
from dataclasses import dataclass, field

KIMI_CODE_BASE_URL = "https://api.kimi.com/coding/v1"
KIMI_CODE_DEFAULT_MODEL = "k3"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"


@dataclass(frozen=True)
class LiveTargetConfig:
    """Connection parameters for a live-model red-team run."""

    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)  # never leak the key into logs via repr
    adapter_type: str = "openai"


def kimi_code_config(model: str = KIMI_CODE_DEFAULT_MODEL) -> LiveTargetConfig:
    """Build the live target config for the Kimi Code managed endpoint."""
    api_key = os.environ.get("KIMI_API_KEY", "").strip()
    return LiveTargetConfig(
        provider="kimi",
        base_url=KIMI_CODE_BASE_URL,
        model=model,
        api_key=api_key,
        adapter_type="openai",
    )


def gemini_config(model: str = GEMINI_DEFAULT_MODEL) -> LiveTargetConfig:
    """Build the live target config for Google AI Studio (native Gemini protocol).

    Honors ``GEMINI_API_KEY`` first, then ``GOOGLE_API_KEY`` — both are
    Gemini-specific, so no cross-provider credential leak is possible.
    """
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip() or (
        os.environ.get("GOOGLE_API_KEY") or ""
    ).strip()
    return LiveTargetConfig(
        provider="gemini",
        base_url=GEMINI_BASE_URL,
        model=model,
        api_key=api_key,
        adapter_type="gemini",
    )


#: provider name -> config factory
LIVE_PROVIDERS = {
    "kimi": kimi_code_config,
    "gemini": gemini_config,
}
