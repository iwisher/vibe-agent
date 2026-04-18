"""Error recovery system for handling LLM and tool errors."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ErrorType(Enum):
    """Types of errors that can occur."""
    PROMPT_TOO_LONG = auto()
    RATE_LIMIT = auto()
    TIMEOUT = auto()
    CONNECTION_ERROR = auto()
    MODEL_ERROR = auto()
    TOOL_ERROR = auto()
    UNKNOWN = auto()


@dataclass
class RetryPolicy:
    """Policy for retrying operations."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    retryable_errors: tuple = (
        ErrorType.RATE_LIMIT,
        ErrorType.TIMEOUT,
        ErrorType.CONNECTION_ERROR,
    )

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for retry attempt."""
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)


@dataclass
class RecoveryResult:
    """Result of error recovery attempt."""
    success: bool
    should_retry: bool
    error_type: ErrorType
    message: str
    modified_context: Optional[list] = None


class ErrorRecovery:
    """Handles error detection, classification, and recovery."""

    # Error patterns for classification
    ERROR_PATTERNS = {
        ErrorType.PROMPT_TOO_LONG: [
            "prompt_too_long",
            "too many tokens",
            "token limit exceeded",
            "context length exceeded",
            "maximum context length",
        ],
        ErrorType.RATE_LIMIT: [
            "rate_limit",
            "rate limit",
            "too many requests",
            "429",
        ],
        ErrorType.TIMEOUT: [
            "timeout",
            "timed out",
            "deadline exceeded",
        ],
        ErrorType.CONNECTION_ERROR: [
            "connection",
            "network",
            "unreachable",
            "refused",
        ],
        ErrorType.MODEL_ERROR: [
            "model not found",
            "invalid model",
            "model error",
        ],
    }

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self.policy = policy or RetryPolicy()
        self._error_handlers: dict[ErrorType, Callable] = {}

    def classify_error(self, error: Exception) -> ErrorType:
        """Classify an error into an ErrorType."""
        error_str = str(error).lower()

        for error_type, patterns in self.ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern in error_str:
                    return error_type

        return ErrorType.UNKNOWN

    async def handle_error(
        self,
        error: Exception,
        context: Optional[list] = None,
    ) -> RecoveryResult:
        """Handle an error and attempt recovery.

        Args:
            error: The exception that occurred
            context: Current conversation context (may be modified)

        Returns:
            RecoveryResult indicating outcome
        """
        error_type = self.classify_error(error)
        logger.info(f"Handling error of type {error_type}: {error}")

        # Check for custom handler
        if error_type in self._error_handlers:
            return await self._error_handlers[error_type](error, context)

        # Default handling
        if error_type == ErrorType.PROMPT_TOO_LONG:
            return await self._handle_prompt_too_long(error, context)

        if error_type == ErrorType.RATE_LIMIT:
            return RecoveryResult(
                success=False,
                should_retry=True,
                error_type=error_type,
                message="Rate limited, will retry with backoff",
            )

        if error_type == ErrorType.TIMEOUT:
            return RecoveryResult(
                success=False,
                should_retry=True,
                error_type=error_type,
                message="Request timed out, will retry",
            )

        if error_type == ErrorType.CONNECTION_ERROR:
            return RecoveryResult(
                success=False,
                should_retry=True,
                error_type=error_type,
                message="Connection error, will retry",
            )

        # Unknown errors - don't retry
        return RecoveryResult(
            success=False,
            should_retry=False,
            error_type=error_type,
            message=f"Unrecoverable error: {error}",
        )

    async def _handle_prompt_too_long(
        self,
        error: Exception,
        context: Optional[list],
    ) -> RecoveryResult:
        """Handle prompt too long error by suggesting context compaction."""
        if context and len(context) > 4:
            # Suggest removing older messages
            return RecoveryResult(
                success=True,
                should_retry=True,
                error_type=ErrorType.PROMPT_TOO_LONG,
                message="Context too long, compaction needed",
                modified_context=context[-4:],  # Keep only recent messages
            )

        return RecoveryResult(
            success=False,
            should_retry=False,
            error_type=ErrorType.PROMPT_TOO_LONG,
            message="Context too long and cannot be compacted further",
        )

    async def execute_with_retry(
        self,
        operation: Callable[[], Coroutine[Any, Any, T]],
        context: Optional[list] = None,
    ) -> T:
        """Execute an operation with retry logic.

        Args:
            operation: Async callable to execute
            context: Current context for potential modification

        Returns:
            Operation result

        Raises:
            The last error if all retries are exhausted
        """
        last_error = None

        for attempt in range(self.policy.max_retries + 1):
            try:
                return await operation()
            except Exception as e:
                last_error = e
                error_type = self.classify_error(e)

                if error_type not in self.policy.retryable_errors:
                    logger.error(f"Non-retryable error: {e}")
                    raise

                if attempt < self.policy.max_retries:
                    delay = self.policy.get_delay(attempt)
                    logger.info(f"Retry {attempt + 1}/{self.policy.max_retries} after {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"All retries exhausted: {e}")

        raise last_error

    def register_handler(
        self,
        error_type: ErrorType,
        handler: Callable[[Exception, Optional[list]], Coroutine[Any, Any, RecoveryResult]],
    ) -> None:
        """Register a custom error handler."""
        self._error_handlers[error_type] = handler
