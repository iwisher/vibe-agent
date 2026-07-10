"""Shared circuit breaker for FlashLLMClient and main LLMClient.

Eliminates the separate code path in FlashLLMClient by injecting the
same CircuitBreaker instance used by the main LLMClient.
"""

from __future__ import annotations

from typing import Any

from vibe.core.model_gateway import CircuitBreaker


class SharedCircuitBreaker:
    """Wrapper that provides circuit breaker sharing between clients.

    Usage:
        cb = CircuitBreaker(threshold=5, cooldown_seconds=60)
        main_client = LLMClient(..., circuit_breaker=cb)
        flash_client = FlashLLMClient(..., circuit_breaker=cb)
    """

    def __init__(self, circuit_breaker: CircuitBreaker | None = None) -> None:
        self._cb = circuit_breaker or CircuitBreaker()

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._cb

    def is_open(self, model: str) -> bool:
        return self._cb.is_open(model)

    def record_success(self, model: str) -> None:
        self._cb.record_success(model)

    def record_failure(self, model: str) -> None:
        self._cb.record_failure(model)

    @classmethod
    def from_config(cls, config: Any) -> "SharedCircuitBreaker":
        """Create from config with shared threshold/cooldown."""
        threshold = getattr(config, "circuit_breaker_threshold", 5)
        cooldown = getattr(config, "circuit_breaker_cooldown", 60.0)
        return cls(CircuitBreaker(threshold=threshold, cooldown_seconds=cooldown))


def patch_flash_client_with_shared_cb(
    flash_client: Any,
    shared_cb: SharedCircuitBreaker | CircuitBreaker,
) -> None:
    """Patch a FlashLLMClient to use a shared circuit breaker.

    Args:
        flash_client: FlashLLMClient instance
        shared_cb: SharedCircuitBreaker or CircuitBreaker instance
    """
    cb = shared_cb.circuit_breaker if isinstance(shared_cb, SharedCircuitBreaker) else shared_cb

    # Store reference and patch methods
    flash_client._circuit_breaker = cb
    flash_client._original_complete = flash_client.complete

    async def _patched_complete(*args, **kwargs):
        model = getattr(flash_client, "model", "flash")
        if cb.is_open(model):
            from vibe.memory.flash_client import FlashLLMResponse

            return FlashLLMResponse(content="", success=False, error="circuit_breaker_open")

        try:
            result = await flash_client._original_complete(*args, **kwargs)
            if result.success:
                cb.record_success(model)
            else:
                cb.record_failure(model)
            return result
        except Exception:
            cb.record_failure(model)
            raise

    flash_client.complete = _patched_complete
