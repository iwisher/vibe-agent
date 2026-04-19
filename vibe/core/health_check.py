"""Model health checking with provider-aware availability detection.

Uses provider adapters to send correctly-formatted probe requests,
supporting both OpenAI-compatible and Anthropic-native endpoints.
"""

from typing import Any

import httpx

from vibe.core.config import VibeConfig
from vibe.core.provider_registry import ProviderRegistry
from vibe.evals.model_registry import ModelRegistry, ModelProfile


class ModelHealthChecker:
    """Checks if a model is available before committing to it.

    Supports multi-provider setups by resolving the correct adapter
    for each model's provider. Falls back to legacy OpenAI-compatible
    behavior when no ProviderRegistry is provided.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        provider_registry: ProviderRegistry | None = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.provider_registry = provider_registry

    def _legacy_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check_available(
        self,
        model_id: str,
        timeout: float = 10.0,
        provider_name: str | None = None,
    ) -> bool:
        """Send a minimal request to verify the model is online.

        If provider_name is given, resolves the adapter from ProviderRegistry.
        Otherwise falls back to legacy OpenAI-compatible probe.

        Returns True if the model responds successfully.
        """
        if self.provider_registry is not None and provider_name is not None:
            return await self._check_with_provider(model_id, timeout, provider_name)
        return await self._check_legacy(model_id, timeout)

    async def _check_with_provider(
        self, model_id: str, timeout: float, provider_name: str
    ) -> bool:
        """Provider-aware health check using adapter-defined probes."""
        provider = self.provider_registry.get(provider_name)
        if provider is None:
            return False

        adapter = provider.create_adapter()
        api_key = provider.resolve_api_key()
        probes = adapter.health_check_endpoints(provider.base_url, model_id)

        for method, url in probes:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method == "GET":
                        headers = {"Content-Type": "application/json"}
                        if api_key:
                            # Auth header varies by adapter; let build_request tell us
                            _, auth_headers, _ = adapter.build_request(
                                base_url=provider.base_url,
                                model=model_id,
                                messages=[],
                                api_key=api_key,
                            )
                            headers.update(auth_headers)
                        response = await client.get(url, headers=headers)
                    else:
                        # POST: use adapter to build a minimal payload
                        _, headers, payload = adapter.build_request(
                            base_url=provider.base_url,
                            model=model_id,
                            messages=[{"role": "user", "content": "."}],
                            max_tokens=1,
                            api_key=api_key,
                        )
                        response = await client.post(url, json=payload, headers=headers)

                if response.status_code < 400:
                    try:
                        data = response.json()
                        if adapter.parse_health_response(method, url, data):
                            return True
                    except Exception:
                        pass
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError):
                continue
            except Exception:
                continue

        return False

    async def _check_legacy(self, model_id: str, timeout: float) -> bool:
        """Legacy OpenAI-compatible health check.

        Kept for backward compatibility when ProviderRegistry is not used.
        """
        # First try the free /v1/models endpoint
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    f"{self.base_url}/v1/models",
                    headers=self._legacy_headers(),
                )
            if response.status_code < 400:
                try:
                    data = response.json()
                    models = data.get("data", [])
                    for m in models:
                        if m.get("id") == model_id:
                            return True
                except Exception:
                    pass
            if response.status_code >= 400:
                return False
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError):
            return False
        except Exception:
            pass

        # Fallback: minimal chat completion probe
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
                    headers=self._legacy_headers(),
                )
            return response.status_code < 400
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError):
            return False
        except Exception:
            return False

    async def resolve_model(
        self,
        config: VibeConfig,
        registry: ModelRegistry | None = None,
    ) -> str:
        """Walk the fallback chain and return the first available model.

        If none are available, returns the default model as a last resort
        (caller should handle potential failure).
        """
        chain = config.get_fallback_chain()
        registry = registry or ModelRegistry()
        provider_registry = self.provider_registry or config.providers

        for model_name in chain:
            profile = registry.get(model_name)
            if profile:
                provider_name = profile.provider
                resolved_model_id = profile.model_id
            else:
                # No profile found: try default provider with model name as ID
                provider_name = "default"
                resolved_model_id = model_name

            if await self.check_available(
                resolved_model_id,
                timeout=config.fallback.health_check_timeout,
                provider_name=provider_name,
            ):
                config.set_resolved_model(model_name)
                return model_name

        # Nothing available; return default and let the caller fail gracefully
        config.set_resolved_model(config.llm.default_model)
        return config.llm.default_model

    async def resolve_with_retry(
        self,
        config: VibeConfig,
        registry: ModelRegistry | None = None,
    ) -> str:
        """Resolve with up to config.fallback.max_retries attempts."""
        for attempt in range(1, config.fallback.max_retries + 1):
            resolved = await self.resolve_model(config, registry=registry)
            # If resolved model is not the default, we found something working
            if resolved != config.llm.default_model:
                return resolved
            # If default itself is available, we're good
            profile = (registry or ModelRegistry()).get(resolved)
            if profile:
                provider_name = profile.provider
                model_id = profile.model_id
            else:
                provider_name = "default"
                model_id = resolved
            if await self.check_available(
                model_id,
                timeout=config.fallback.health_check_timeout,
                provider_name=provider_name,
            ):
                return resolved
            # Otherwise retry after a short delay
            import asyncio

            await asyncio.sleep(2 ** (attempt - 1))

        return config.llm.default_model
