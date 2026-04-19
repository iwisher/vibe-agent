import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

import httpx
from vibe.core.error_recovery import ErrorRecovery, RetryPolicy
from vibe.core.llm_types import ErrorType, LLMResponse


RequestHook = Callable[[Dict[str, Any], str], None]
ResponseHook = Callable[[LLMResponse, str], None]


class ErrorType(Enum):
    """Types of errors that can occur during LLM communication."""
    NONE = auto()
    HTTP_ERROR = auto()
    JSON_DECODE_ERROR = auto()
    NETWORK_ERROR = auto()
    CONNECTION_ERROR = auto()
    TIMEOUT_ERROR = auto()
    RATE_LIMIT_ERROR = auto()
    AUTHENTICATION_ERROR = auto()
    SERVER_ERROR = auto()
    UNKNOWN_ERROR = auto()
    MODEL_UNAVAILABLE = auto()


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
        self._states: dict[str, CircuitBreakerState] = {}

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


@dataclass
class LLMResponse:
    """Standardized response from LLM."""
    content: str
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any] | None] = None
    error: str | None = None
    error_type: ErrorType = ErrorType.NONE
    actionable_hint: str | None = None
    model_used: str | None = None  # Actual model after fallback resolution

    @property
    def is_error(self) -> bool:
        return self.error is not None


class LLMClient:
    """Gateway for communicating with LLM models."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "default",
        api_key: str | None = None,
        timeout: float = 300.0,
        retry_policy: RetryPolicy | None = None,
        fallback_chain: list[str] | None = None,
        auto_fallback: bool = False,
        circuit_breaker: CircuitBreaker | None = None,
        on_request: RequestHook | None = None,
        on_response: ResponseHook | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.timeout = timeout
        self.recovery = ErrorRecovery(retry_policy)
        self.client = httpx.AsyncClient(timeout=self.timeout)
        self.fallback_chain = fallback_chain or []
        self.auto_fallback = auto_fallback
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.on_request = on_request
        self.on_response = on_response

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any] | None] = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Sends a completion request with built-in retry and optional model fallback."""

        models_to_try = [self.model] + [
            m for m in self.fallback_chain if m != self.model
        ]

        last_error: LLMResponse | None = None

        for attempt_model in models_to_try:
            # Circuit breaker: skip models that are continuously failing
            if self.circuit_breaker.is_open(attempt_model):
                continue

            result = await self._try_complete(
                attempt_model,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            )
            if not result.is_error:
                self.circuit_breaker.record_success(attempt_model)
                result.model_used = attempt_model
                return result

            self.circuit_breaker.record_failure(attempt_model)

            # If this is a model-unavailability error and fallback is enabled, continue
            if self.auto_fallback and result.error_type in (
                ErrorType.SERVER_ERROR,
                ErrorType.HTTP_ERROR,
                ErrorType.MODEL_UNAVAILABLE,
                ErrorType.AUTHENTICATION_ERROR,  # 401/403 may indicate model-specific unavailability
            ):
                # Fallback on all 4xx/5xx except rate limit (429)
                if result.error_type != ErrorType.RATE_LIMIT_ERROR:
                    last_error = result
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
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any] | None] = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Single attempt at a completion request for a specific model."""

        async def _make_request():
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                payload["max_tokens"] = max_tokens
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice

            if self.on_request:
                self.on_request(payload, model)

            response = await self.client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=self._get_headers(),
            )
            response.raise_for_status()
            data = response.json()

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})

            return LLMResponse(
                content=message.get("content", ""),
                usage=data.get("usage", {}),
                finish_reason=choice.get("finish_reason"),
                tool_calls=message.get("tool_calls"),
            )

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

            result = LLMResponse(
                content="",
                error=str(e),
                error_type=error_type,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result
        except httpx.TimeoutException as e:
            result = LLMResponse(
                content="",
                error=str(e),
                error_type=ErrorType.TIMEOUT_ERROR,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result
        except (httpx.NetworkError, httpx.ConnectError) as e:
            result = LLMResponse(
                content="",
                error=str(e),
                error_type=ErrorType.NETWORK_ERROR,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result
        except Exception as e:
            result = LLMResponse(
                content="",
                error=str(e),
                error_type=ErrorType.UNKNOWN_ERROR,
                actionable_hint=self.recovery.handle_error(e),
            )
            if self.on_response:
                self.on_response(result, model)
            return result

    async def structured_output(
        self,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any],
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """
        Forces the LLM to provide structured JSON output matching the schema.
        Prepends a system message for guidance.
        """
        system_instruction = (
            "Return only valid JSON that matches this schema exactly:\n"
            f"{json.dumps(output_schema, indent=2)}\n\n"
            "Do not include any conversational text or markdown formatting tags like ```json."
        )
        
        # Insert system instruction at the beginning
        full_messages = [{"role": "system", "content": system_instruction}] + messages
        
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
        """Closes the underlying HTTP client."""
        await self.client.aclose()
