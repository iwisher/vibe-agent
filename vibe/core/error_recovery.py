import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, Type, TypeVar, Union

T = TypeVar("T")

@dataclass
class RetryPolicy:
    """Policy for retrying failed operations."""
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,)

class ErrorRecovery:
    """Handles error recovery and retries with exponential backoff."""

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self.policy = policy or RetryPolicy()

    async def execute_with_retry(
        self,
        coroutine_factory: Callable[[], Coroutine[Any, Any, T]],
        is_retryable: Optional[Callable[[Exception], bool]] = None,
    ) -> T:
        """
        Executes a coroutine with retry logic.
        
        Args:
            coroutine_factory: A callable that returns a coroutine to execute.
            is_retryable: An optional callable that takes an exception and returns
                         True if it's retryable.
        """
        last_exception = None
        delay = self.policy.initial_delay

        for attempt in range(self.policy.max_retries + 1):
            try:
                return await coroutine_factory()
            except self.policy.retryable_exceptions as e:
                last_exception = e
                
                if attempt == self.policy.max_retries:
                    break

                if is_retryable and not is_retryable(e):
                    raise e

                # Calculate delay with exponential backoff
                sleep_time = delay
                if self.policy.jitter:
                    sleep_time = delay * (0.5 + random.random())
                
                await asyncio.sleep(min(sleep_time, self.policy.max_delay))
                delay *= self.policy.backoff_factor
            except Exception as e:
                # Non-retryable exception
                raise e

        raise last_exception or RuntimeError("Retry loop exhausted without exception")

    def handle_error(self, error: Exception) -> str:
        """Returns an actionable hint based on the error."""
        error_name = type(error).__name__
        error_msg = str(error)
        
        # This can be expanded based on specific error types encountered
        if "timeout" in error_msg.lower():
            return "The request timed out. Try increasing the timeout or reducing the request size."
        if "rate limit" in error_msg.lower() or "429" in error_msg:
            return "Rate limit exceeded. Consider slowing down requests or checking your quota."
        if "connection" in error_msg.lower():
            return "Connection error. Check your network and the server status."
        if "auth" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
            return "Authentication failed. Check your API key or credentials."
        
        return f"An unexpected error occurred: {error_name}. Please check the logs for details."
