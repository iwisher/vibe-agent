"""Tests for shared circuit breaker between LLMClient and FlashLLMClient."""

import pytest

from vibe.core.model_gateway import CircuitBreaker
from vibe.core.shared_circuit_breaker import (
    SharedCircuitBreaker,
    patch_flash_client_with_shared_cb,
)


class TestSharedCircuitBreaker:
    def test_default_creation(self):
        scb = SharedCircuitBreaker()
        assert scb.circuit_breaker is not None
        assert not scb.is_open("any_model")

    def test_with_existing_breaker(self):
        cb = CircuitBreaker(threshold=3, cooldown_seconds=30)
        scb = SharedCircuitBreaker(cb)
        assert scb.circuit_breaker == cb

    def test_record_failure_opens(self):
        cb = CircuitBreaker(threshold=2, cooldown_seconds=60)
        scb = SharedCircuitBreaker(cb)

        assert not scb.is_open("test_model")
        scb.record_failure("test_model")
        assert not scb.is_open("test_model")
        scb.record_failure("test_model")
        assert scb.is_open("test_model")

    def test_record_success_resets(self):
        cb = CircuitBreaker(threshold=2, cooldown_seconds=60)
        scb = SharedCircuitBreaker(cb)

        scb.record_failure("test_model")
        scb.record_success("test_model")
        scb.record_failure("test_model")
        # After success reset, one failure shouldn't open
        assert not scb.is_open("test_model")

    def test_from_config(self):
        class FakeConfig:
            circuit_breaker_threshold = 10
            circuit_breaker_cooldown = 120.0

        scb = SharedCircuitBreaker.from_config(FakeConfig())
        assert scb.circuit_breaker.threshold == 10
        assert scb.circuit_breaker.cooldown_seconds == 120.0


class TestPatchFlashClient:
    def test_patch_with_shared_cb(self):
        class FakeFlashClient:
            def __init__(self):
                self.model = "phi3:mini"
                self.complete_called = False

            async def complete(self, prompt, **kwargs):
                self.complete_called = True
                return FakeResponse("hello", True)

        class FakeResponse:
            def __init__(self, content, success):
                self.content = content
                self.success = success

        cb = CircuitBreaker(threshold=2, cooldown_seconds=60)
        flash = FakeFlashClient()
        patch_flash_client_with_shared_cb(flash, cb)

        # Should work normally
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(flash.complete("test"))
        finally:
            loop.close()
        assert result.content == "hello"
        assert flash.complete_called is True

    def test_patch_blocks_when_open(self):
        class FakeFlashClient:
            def __init__(self):
                self.model = "phi3:mini"

            async def complete(self, prompt, **kwargs):
                return FakeResponse("hello", True)

        class FakeResponse:
            def __init__(self, content, success, error=None):
                self.content = content
                self.success = success
                self.error = error

        cb = CircuitBreaker(threshold=1, cooldown_seconds=60)
        cb.record_failure("phi3:mini")  # Open the breaker

        flash = FakeFlashClient()
        patch_flash_client_with_shared_cb(flash, cb)

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(flash.complete("test"))
        finally:
            loop.close()
        assert result.success is False
        assert result.error == "circuit_breaker_open"

    def test_patch_records_failure_on_error(self):
        class FakeFlashClient:
            def __init__(self):
                self.model = "phi3:mini"

            async def complete(self, prompt, **kwargs):
                raise RuntimeError("boom")

        cb = CircuitBreaker(threshold=2, cooldown_seconds=60)
        flash = FakeFlashClient()
        patch_flash_client_with_shared_cb(flash, cb)

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError):
                loop.run_until_complete(flash.complete("test"))
        finally:
            loop.close()
        assert cb.is_open("phi3:mini") is False  # 1 failure, threshold=2
        assert cb._state("phi3:mini").consecutive_failures == 1
