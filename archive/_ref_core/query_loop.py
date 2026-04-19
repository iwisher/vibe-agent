"""Query loop implementation for the Claude Code Clone."""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import AsyncIterator, Callable, Optional

from ..utils.llm_client import LLMClient, ErrorType, ErrorAction
from ..tools.tool_system import ToolSystem, ToolResult
from .context_compactor import ContextCompactor
from .error_recovery import ErrorRecovery, RetryPolicy


class QueryState(Enum):
    """State of the query loop."""
    IDLE = auto()
    PROCESSING = auto()
    TOOL_EXECUTION = auto()
    ERROR_RECOVERY = auto()
    COMPLETED = auto()
    STOPPED = auto()


@dataclass
class Metrics:
    """Performance metrics for a query."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    tokens_per_second: float = 0.0


@dataclass
class Message:
    """A message in the conversation."""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_calls: Optional[list] = None
    tool_results: Optional[list] = None
    model_version: Optional[str] = None  # Track which model generated this message


@dataclass
class QueryResult:
    """Result of a query iteration."""
    response: str
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Optional[Exception] = None
    context_truncated: bool = False
    metrics: Optional[Metrics] = None  # Performance metrics


class QueryLoop:
    """Main query loop that manages conversation flow."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_system: ToolSystem,
        context_compactor: Optional[ContextCompactor] = None,
        error_recovery: Optional[ErrorRecovery] = None,
        max_iterations: int = 50,
        max_context_tokens: int = 100000,
    ):
        self.llm = llm_client
        self.tools = tool_system
        self.compactor = context_compactor or ContextCompactor(max_tokens=max_context_tokens)
        self.error_recovery = error_recovery or ErrorRecovery(RetryPolicy())
        self.max_iterations = max_iterations
        self.messages: list[Message] = []
        self._running = False
        self._tool_handlers: dict[str, Callable] = {}

    def register_tool_handler(self, tool_name: str, handler: Callable) -> None:
        """Register a handler for a specific tool."""
        self._tool_handlers[tool_name] = handler

    def set_model(self, model: str) -> str:
        """Set the model to use and return the previous model."""
        old_model = self.llm.get_model()
        self.llm.set_model(model)
        # Add a system message noting the model change
        self.messages.append(Message(
            role="system",
            content=f"Model switched from '{old_model}' to '{model}'",
            model_version=model
        ))
        return old_model

    def get_model(self) -> str:
        """Get the current model."""
        return self.llm.get_model()

    async def run(self, initial_query: Optional[str] = None) -> AsyncIterator[QueryResult]:
        """Run the query loop, yielding results as they become available."""
        self._running = True

        if initial_query:
            self.messages.append(Message(role="user", content=initial_query))

        iteration = 0
        while self._running and iteration < self.max_iterations:
            iteration += 1

            try:
                # Compact context if needed
                if self.compactor.should_compact(self.messages):
                    self.messages = await self.compactor.compact(self.messages)
                    yield QueryResult(
                        response="",
                        context_truncated=True
                    )

                # Build messages for LLM
                llm_messages = self._build_llm_messages()

                # Capture timing for metrics
                start_time = time.time()
                
                # Get response from LLM with retry
                response = await self.error_recovery.execute_with_retry(
                    lambda: self.llm.complete(llm_messages, tools=self.tools.get_tool_schemas())
                )
                
                elapsed_seconds = time.time() - start_time
                
                # Calculate metrics
                metrics = None
                if not response.is_error:
                    usage = response.usage
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
                    tokens_per_second = completion_tokens / elapsed_seconds if elapsed_seconds > 0 else 0
                    
                    metrics = Metrics(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        elapsed_seconds=elapsed_seconds,
                        tokens_per_second=tokens_per_second
                    )
                
                # Check for LLM errors with enhanced error handling
                if response.is_error:
                    error_msg = response.error
                    # Add actionable guidance based on error type
                    if response.error_type == ErrorType.AUTHENTICATION_ERROR:
                        error_msg += "\n[Hint: Check your API key with /model --api-key <key>]"
                    elif response.error_type == ErrorType.RATE_LIMIT_ERROR:
                        error_msg += "\n[Hint: Rate limited. Waiting before retry...]"
                    elif response.error_type == ErrorType.TIMEOUT_ERROR:
                        error_msg += "\n[Hint: Request timed out. Consider reducing context size with /compact]"
                    elif response.error_type == ErrorType.NETWORK_ERROR:
                        error_msg += "\n[Hint: Check your network connection]"
                    elif response.error_type == ErrorType.SERVER_ERROR:
                        error_msg += "\n[Hint: LLM server error. Retry or check server status]"

                    yield QueryResult(
                        response="",
                        error=Exception(error_msg) if isinstance(error_msg, str) else error_msg
                    )
                    break
                
                # Handle empty content and no tool calls
                if not response.content and not response.tool_calls:
                    yield QueryResult(response="", error="LLM returned empty response")
                    break

                # Handle tool calls if present
                if response.tool_calls:
                    tool_results = await self._execute_tool_calls(response.tool_calls)
                    self.messages.append(Message(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                        model_version=self.llm.get_model()
                    ))

                    # Add tool results to messages
                    for result in tool_results:
                        self.messages.append(Message(
                            role="tool",
                            content=result.content if result.success else result.error,
                        ))

                    yield QueryResult(
                        response=response.content or "",
                        tool_results=tool_results,
                        metrics=metrics
                    )
                else:
                    # Regular response
                    self.messages.append(Message(
                        role="assistant", 
                        content=response.content,
                        model_version=self.llm.get_model()
                    ))
                    yield QueryResult(response=response.content, metrics=metrics)

                    # Stop if no tool calls (conversation turn complete)
                    break

            except Exception as e:
                # Attempt error recovery
                recovery = await self.error_recovery.handle_error(e, self.messages)
                if recovery.should_retry:
                    # Will retry on next iteration
                    continue
                else:
                    yield QueryResult(response="", error=e)
                    break

    async def _execute_tool_calls(self, tool_calls: list) -> list[ToolResult]:
        """Execute a list of tool calls."""
        import json
        results = []
        for call in tool_calls:
            try:
                # Handle both dict and object formats
                if isinstance(call, dict):
                    call_name = call.get("name") or call.get("function", {}).get("name")
                    arguments = call.get("arguments") or call.get("function", {}).get("arguments", "{}")
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                else:
                    call_name = call.name
                    arguments = call.arguments
                
                # Check for custom handler
                if call_name in self._tool_handlers:
                    result = await self._tool_handlers[call_name](arguments)
                else:
                    result = await self.tools.execute_tool(call_name, **arguments)
                results.append(result)
            except Exception as e:
                results.append(ToolResult(
                    success=False,
                    content=None,
                    error=f"Tool execution failed: {e}",
                ))
        return results

    def _build_llm_messages(self) -> list[dict]:
        """Convert internal messages to LLM format."""
        return [
            {
                "role": msg.role,
                "content": msg.content,
                **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
            }
            for msg in self.messages
        ]

    def stop(self) -> None:
        """Stop the query loop."""
        self._running = False

    def add_user_message(self, content: str) -> None:
        """Add a user message to the conversation."""
        self.messages.append(Message(role="user", content=content))

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.messages.clear()
