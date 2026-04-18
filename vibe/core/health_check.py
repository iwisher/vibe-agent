"""Model health checking with availability detection and fallback resolution.

Detects Applesay-specific "无可用渠道" errors and generic 4xx/5xx failures.
"""

from typing import Any, Dict, List, Optional

import httpx

from vibe.core.config import VibeConfig
from vibe.evals.model_registry import ModelRegistry, ModelProfile


# Applesay-specific error indicating no available channel for the model
_UNAVAILABLE_CHANNEL_MSG = "无可用渠道"


class ModelHealthChecker:
    """Checks if a model is available before committing to it."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = (base_url or "http://ai-api.applesay.cn").rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check_available(
        self,
        model_id: str,
        timeout: float = 10.0,
    ) -> bool:
        """Send a minimal request to verify the model is online.

        Returns True if the model responds successfully, False if:
        - HTTP 4xx/5xx (including "无可用渠道")
        - Network/timeout errors
        """
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "."}],
            "max_tokens": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=self._get_headers(),
                )
            if response.status_code >= 400:
                body = response.text
                if _UNAVAILABLE_CHANNEL_MSG in body:
                    return False
                # Any 4xx/5xx is treated as unavailable for fallback purposes
                return False
            return True
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError):
            return False
        except Exception:
            # Conservative: if we can't determine status, treat as unavailable
            return False

    async def resolve_model(
        self,
        config: VibeConfig,
        registry: Optional[ModelRegistry] = None,
    ) -> str:
        """Walk the fallback chain and return the first available model.

        If none are available, returns the default model as a last resort
        (caller should handle potential failure).
        """
        chain = config.get_fallback_chain()
        registry = registry or ModelRegistry()

        for model_name in chain:
            profile = registry.get(model_name)
            model_id = profile.model_id if profile else model_name
            if await self.check_available(model_id, timeout=config.fallback.health_check_timeout):
                config.set_resolved_model(model_name)
                return model_name

        # Nothing available; return default and let the caller fail gracefully
        config.set_resolved_model(config.llm.default_model)
        return config.llm.default_model

    async def resolve_with_retry(
        self,
        config: VibeConfig,
        registry: Optional[ModelRegistry] = None,
    ) -> str:
        """Resolve with up to config.fallback.max_retries attempts."""
        for attempt in range(1, config.fallback.max_retries + 1):
            resolved = await self.resolve_model(config, registry=registry)
            # If resolved model is not the default, we found something working
            if resolved != config.llm.default_model:
                return resolved
            # If default itself is available, we're good
            profile = (registry or ModelRegistry()).get(resolved)
            model_id = profile.model_id if profile else resolved
            if await self.check_available(model_id, timeout=config.fallback.health_check_timeout):
                return resolved
            # Otherwise retry after a short delay
            import asyncio

            await asyncio.sleep(2 ** (attempt - 1))

        return config.llm.default_model


def is_unavailable_error(error_text: str) -> bool:
    """Check if an error text indicates the model channel is unavailable."""
    if not error_text:
        return False
    return _UNAVAILABLE_CHANNEL_MSG in error_text or "no available channel" in error_text.lower()
