import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from vibe.core.error_recovery import ErrorRecovery, RetryPolicy
from vibe.core.llm_types import ErrorType, LLMResponse

RequestHook = Callable[[Dict[str, Any], str], None]
ResponseHook = Callable[[LLMResponse, str], None]


@dataclass
class CircuitBreakerState:
    """Per-model circuit breaker state."""

    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    open: bool = False


class CircuitBreaker:
    """Simple circuit breaker for LLM model endpoints.
    Opens after `threshold` consecutive failures, stays open for `cooldown_seconds`,
    then allows a single half-open probe. If the probe succeeds, the breaker closes.
    """

    def __init__(self, threshold: int = 5, cooldown_seconds: float = 60.0):
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._states: Dict[str, CircuitBreakerState] = {}

    def _state(self, model: str) -> CircuitBreakerState:
        if model not in self._states:
            self._states[model] = CircuitBreakerState()
        return self._states[model]

    def is_open(self, model: str) -> bool:
        """Return True if the circuit breaker is open for this model."""
        state = self._state(model)
        if not state.open:
            return False
        # Check if cooldown has elapsed (half-open)
        if time.time() - state.last_failure_time >= self.cooldown_seconds:
            state.open = False  # half-open: allow one probe
            return False
        return True

    def record_success(self, model: str) -> None:
        """Reset failure count on success."""
        state = self._state(model)
        state.consecutive_failures = 0
        state.open = False

    def record_failure(self, model: str) -> None:
        """Increment failure count and open breaker if threshold reached."""
        state = self._state(model)
        state.consecutive_failures += 1
        state.last_failure_time = time.time()
        if state.consecutive_failures >= self.threshold:
            state.open = True


class LLMClient:
    """Gateway for communicating with LLM models."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "default",
        api_key: Optional[str] = None,
        timeout: float = 300.0,
        retry_policy: Optional[RetryPolicy] = None,
        fallback_chain: Optional[List[str]] = None,
        auto_fallback: bool = False,
        circuit_breaker: Optional[CircuitBreaker] = None,
        on_request: Optional[RequestHook] = None,
        on_response: Optional[ResponseHook] = None,
        adapter: Any | None = None,
        registry: Any | None = None,
        client: httpx.AsyncClient | None = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown: float = 60.0,
        logger: Any | None = None,
        debug: bool = False,
    ):
        """Initialize the LLM client.

        Args:
            base_url: The base URL for the LLM API.
            model: The model identifier.
            api_key: Optional API key for authentication.
            timeout: Request timeout in seconds. Only applies when *client* is not provided.
            retry_policy: Optional retry policy for failed requests.
            fallback_chain: List of fallback model names.
            auto_fallback: Whether to auto-fallback on server errors.
            circuit_breaker: Optional circuit breaker instance.
            on_request: Optional callback for outgoing requests.
            on_response: Optional callback for incoming responses.
            adapter: Optional request/response adapter.
            registry: Optional ModelRegistry for multi-provider fallback.
            client: Optional shared httpx.AsyncClient. If provided, the *timeout* parameter
                is ignored and the caller is responsible for closing the client.
            circuit_breaker_threshold: Consecutive failures before opening breaker.
            circuit_breaker_cooldown: Seconds to stay open before probing again.
            logger: Optional SessionLogger for logging requests/responses.
            debug: If True, print request URL and redacted headers to stderr.
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.timeout = timeout
        self.recovery = ErrorRecovery(retry_policy)
        self.client = client or httpx.AsyncClient(timeout=self.timeout)
        self._owns_client = client is None
        self.fallback_chain = fallback_chain or []
        self.auto_fallback = auto_fallback
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            threshold=circuit_breaker_threshold, cooldown_seconds=circuit_breaker_cooldown
        )
        self.on_request = on_request
        self.on_response = on_response
        self.registry = registry
        self.logger = logger
        self.debug = debug
        # Adapter: default to OpenAI-compatible for backward compatibility
        if adapter is None:
            from vibe.adapters.openai import OpenAIAdapter
            adapter = OpenAIAdapter()
        self.adapter = adapter

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Sends a completion request with built-in retry and optional model fallback."""

        models_to_try = [self.model] + [
            m for m in self.fallback_chain if m != self.model
        ]

        last_error: Optional[LLMResponse] = None

        for attempt_model in models_to_try:
            # Circuit breaker: skip models that are continuously failing
            if self.circuit_breaker.is_open(attempt_model):
                if self.debug:
                    print(
                        f"[vibe-debug] SKIP model={attempt_model} reason=circuit_breaker_open",
                        file=sys.stderr,
                    )
                continue

            # Resolve connection details for this model attempt
            base_url = self.base_url
            api_key = self.api_key
            adapter = self.adapter
            extra_headers = {}
            model_id = attempt_model

            if self.registry:
                profile = self.registry.get(attempt_model)
                if profile:
                    base_url = profile.base_url
                    api_key = profile.resolve_api_key()
                    model_id = profile.model_id
                    extra_headers = profile.extra_headers
                    from vibe.adapters.registry import get_adapter

                    adapter = get_adapter(profile.adapter_type)()

            result = await self._try_complete(
                model_id,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                base_url=base_url,
                api_key=api_key,
                adapter=adapter,
                extra_headers=extra_headers,
            )
            if not result.is_error:
                self.circuit_breaker.record_success(attempt_model)
                result.model_used = attempt_model
                return result

            self.circuit_breaker.record_failure(attempt_model)

            if self.debug:
                print(
                    f"[vibe-debug] FAIL model={attempt_model} "
                    f"error_type={result.error_type.name} "
                    f"error={result.error[:200]}",
                    file=sys.stderr,
                )

            # If this is a model-unavailability error and fallback is enabled, continue
            if self.auto_fallback and result.error_type in (
                ErrorType.SERVER_ERROR,
                ErrorType.HTTP_ERROR,
                ErrorType.MODEL_UNAVAILABLE,
                ErrorType.AUTHENTICATION_ERROR,  # 401/403 may indicate model-specific unavailability
                ErrorType.NETWORK_ERROR,
                ErrorType.TIMEOUT_ERROR,
            ):
                # Fallback on all 4xx/5xx except rate limit (429)
                if result.error_type != ErrorType.RATE_LIMIT_ERROR:
                    last_error = result
                    if self.debug:
                        print(
                            f"[vibe-debug] FALLBACK from={attempt_model} "
                            f"to=next_model reason={result.error_type.name}",
                            file=sys.stderr,
                        )
                    continue

            # Rate limit or other non-recoverable errors — don't fallback
            return result

        # All models exhausted
        if last_error:
            last_error.error = f"All models exhausted. Last error: {last_error.error}"
            last_error.error_type = ErrorType.MODEL_UNAVAILABLE
            return last_error

        return LLMResponse(
            content="",
            error="No models available in fallback chain",
            error_type=ErrorType.MODEL_UNAVAILABLE,
        )

    async def _try_complete(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        adapter: Any | None = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> LLMResponse:
        """Single attempt at a completion request for a specific model."""

        # Use overrides or defaults
        eff_base_url = base_url or self.base_url
        eff_api_key = api_key or self.api_key
        eff_adapter = adapter or self.adapter
        eff_extra_headers = extra_headers or {}

        async def _make_request():
            url, headers, payload = eff_adapter.build_request(
                base_url=eff_base_url,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                api_key=eff_api_key,
            )

            # Merge extra headers from provider profile
            if eff_extra_headers:
                headers.update(eff_extra_headers)

            if self.debug:
                safe_headers = {}
                for k, v in headers.items():
                    k_lower = k.lower()
                    if any(p in k_lower for p in ["auth", "key", "token", "secret"]):
                        val = str(v)
                        if len(val) <= 15:
                            safe_headers[k] = "***"
                        else:
                            safe_headers[k] = val[:12] + "..." + val[-4:]
                    else:
                        safe_headers[k] = v
                print(f"[vibe-debug] POST {url}", file=sys.stderr)
                print(f"[vibe-debug] headers={safe_headers}", file=sys.stderr)
                print(
                    f"[vibe-debug] model={payload.get('model')} "
                    f"messages={len(payload.get('messages', []))}",
                    file=sys.stderr,
                )

            if self.logger:
                self.logger.debug(f"LLM Request: model={model} url={url} messages={len(messages)}")

            if self.on_request:
                self.on_request(payload, model)

            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

            res = eff_adapter.parse_response(data)
            if self.logger:
                if res.is_error:
                    self.logger.error(f"LLM Error Response: {res.error}")
                else:
                    self.logger.debug(f"LLM Success Response: {res.content[:200]}...")
            
            return res

        try:
            result = await self.recovery.execute_with_retry(_make_request)
            if self.on_response:
                self.on_response(result, model)
            return result
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            error_type = ErrorType.HTTP_ERROR
            if status == 401 or status == 403:
                error_type = ErrorType.AUTHENTICATION_ERROR
            elif status == 429:
                error_type = ErrorType.RATE_LIMIT_ERROR
            elif status >= 500:
                error_type = ErrorType.SERVER_ERROR

            # Try to extract detailed error from response body
            detail = str(e)
            try:
                body = e.response.text
                if body:
                    # Many APIs return {"error": {"message": "...", "type": "..."}}
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        err_obj = parsed.get("error", parsed)
                        if isinstance(err_obj, dict):
                            msg = err_obj.get("message", "")
                            err_type = err_obj.get("type", "")
                            if msg:
                                detail = f"[{status}] {msg}"
                                if err_type:
                                    detail += f" (type: {err_type})"
                        else:
                            detail = f"[{status}] {err_obj}"
                    else:
                        detail = f"[{status}] {body[:500]}"
                else:
                    detail = f"[{status}] Empty response body"
            except Exception:
                body = e.response.text or ""
                detail = f"[{status}] {body[:500]}"

            if self.debug:
                print(
                    f"[vibe-debug] ERROR model={model} status={status} "
                    f"error_type={error_type.name}",
                    file=sys.stderr,
                )
                print(f"[vibe-debug] detail={detail}", file=sys.stderr)
                hint = self.recovery.handle_error(e)
                if hint:
                    print(f"[vibe-debug] hint={hint}", file=sys.stderr)

            result = LLMResponse(
                content="",
                error=detail,
                error_type=error_type,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result
        except httpx.TimeoutException as e:
            detail = f"[TIMEOUT] {e}"
            if self.debug:
                print(
                    f"[vibe-debug] ERROR model={model} error_type=TIMEOUT "
                    f"detail={detail}",
                    file=sys.stderr,
                )
            result = LLMResponse(
                content="",
                error=detail,
                error_type=ErrorType.TIMEOUT_ERROR,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result
        except (httpx.NetworkError, httpx.ConnectError) as e:
            detail = f"[NETWORK] {e}"
            if self.debug:
                print(
                    f"[vibe-debug] ERROR model={model} error_type=NETWORK "
                    f"detail={detail}",
                    file=sys.stderr,
                )
            result = LLMResponse(
                content="",
                error=detail,
                error_type=ErrorType.NETWORK_ERROR,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result
        except Exception as e:
            detail = f"[{type(e).__name__}] {e}"
            if self.debug:
                print(
                    f"[vibe-debug] ERROR model={model} error_type=UNKNOWN "
                    f"detail={detail}",
                    file=sys.stderr,
                )
            result = LLMResponse(
                content="",
                error=detail,
                error_type=ErrorType.UNKNOWN_ERROR,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result

    async def structured_output(
        self,
        messages: List[Dict[str, Any]],
        output_schema: Dict[str, Any],
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Forces the LLM to provide structured JSON output matching the schema.
        Prepends a system message for guidance.
        """
        system_instruction = (
            "Return only valid JSON that matches this schema exactly:\n"
            f"{json.dumps(output_schema, indent=2)}\n\n"
            "Do not include any conversational text or markdown formatting tags like ```json."
        )

        # Use adapter to properly handle system messages
        system_content, remaining_messages = self.adapter.extract_system_messages(messages)
        if system_content:
            system_instruction = system_content + "\n\n" + system_instruction

        full_messages = [{"role": "system", "content": system_instruction}] + remaining_messages

        # Ensure we have a shallow copy in case the adapter mutates during complete()
        full_messages = list(full_messages)

        response = await self.complete(
            messages=full_messages,
            temperature=temperature,
        )

        if response.is_error:
            raise RuntimeError(f"LLM failed to provide structured output: {response.error}. Hint: {response.actionable_hint}")

        # Basic cleanup of markdown markers if the LLM ignores instructions
        content = response.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                content = "\n".join(lines[1:-1])
            else:
                content = content.strip("`")
        content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM output was not valid JSON: {content}") from e

    async def close(self):
        """Closes the underlying HTTP client if we created it."""
        if self._owns_client:
            await self.client.aclose()
