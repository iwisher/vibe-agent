"""Worker agent that runs in isolated context."""

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from ..core.query_loop import QueryLoop, Message


class WorkerState(Enum):
    """State of a worker agent."""
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    ERROR = auto()
    STOPPED = auto()


@dataclass
class WorkerResult:
    """Result from a worker's task execution."""
    success: bool
    output: str
    error: Optional[str] = None
    messages_exchanged: int = 0
    tool_calls_made: int = 0
    worker_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class Worker:
    """An isolated worker agent with its own context.
    
    Each worker maintains:
    - Isolated message history (no contamination from other workers)
    - Independent tool execution context
    - Separate state tracking
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        llm_client=None,
        tool_system=None,
        max_iterations: int = 10,
    ):
        self.worker_id = worker_id or f"worker_{uuid.uuid4()[:8]}"
        self.llm_client = llm_client
        self.tool_system = tool_system
        self.max_iterations = max_iterations
        
        self.state = WorkerState.IDLE
        self._messages: List[Message] = []
        self._iteration_count = 0
        self._tool_calls_made = 0
        self._stop_requested = False
        
        # Create isolated query loop
        self._query_loop: Optional[QueryLoop] = None
        if llm_client and tool_system:
            self._query_loop = QueryLoop(
                llm_client=llm_client,
                tool_system=tool_system,
                max_iterations=max_iterations,
            )

    async def run_task(
        self,
        task_description: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkerResult:
        """Run a task in isolated context.
        
        Args:
            task_description: What the worker should do
            context: Additional context (files, previous results, etc.)
            
        Returns:
            WorkerResult with output and metadata
        """
        context = context or {}
        self.state = WorkerState.RUNNING
        self._stop_requested = False
        
        try:
            # Build the prompt with context
            prompt = self._build_prompt(task_description, context)
            
            # Execute using query loop if available
            if self._query_loop:
                return await self._run_with_query_loop(prompt, context)
            else:
                return await self._run_simple(prompt, context)
                
        except Exception as e:
            self.state = WorkerState.ERROR
            return WorkerResult(
                success=False,
                output="",
                error=str(e),
                worker_id=self.worker_id,
            )

    async def _run_with_query_loop(
        self,
        prompt: str,
        context: Dict[str, Any],
    ) -> WorkerResult:
        """Run task using the query loop for full tool support."""
        output_parts = []
        tool_calls = 0
        
        try:
            # Clear any previous state
            self._query_loop.clear_history()
            
            # Add system context if provided
            if "system_context" in context:
                self._query_loop.messages.append(Message(
                    role="system",
                    content=context["system_context"],
                ))
            
            # Add files context
            if "files" in context:
                files_content = "\n\n".join(
                    f"File: {path}\n```\n{content}\n```"
                    for path, content in context["files"].items()
                )
                self._query_loop.messages.append(Message(
                    role="system",
                    content=f"Relevant files:\n{files_content}",
                ))
            
            # Run the query
            async for result in self._query_loop.run(initial_query=prompt):
                if self._stop_requested:
                    break
                
                if result.error:
                    self.state = WorkerState.ERROR
                    return WorkerResult(
                        success=False,
                        output="".join(output_parts),
                        error=str(result.error),
                        messages_exchanged=len(self._query_loop.messages),
                        tool_calls_made=tool_calls,
                        worker_id=self.worker_id,
                    )
                
                if result.response:
                    output_parts.append(result.response)
                
                if result.tool_results:
                    tool_calls += len(result.tool_results)
            
            self.state = WorkerState.COMPLETED
            return WorkerResult(
                success=True,
                output="".join(output_parts),
                messages_exchanged=len(self._query_loop.messages),
                tool_calls_made=tool_calls,
                worker_id=self.worker_id,
            )
            
        except Exception as e:
            self.state = WorkerState.ERROR
            return WorkerResult(
                success=False,
                output="".join(output_parts),
                error=str(e),
                messages_exchanged=len(self._query_loop.messages),
                tool_calls_made=tool_calls,
                worker_id=self.worker_id,
            )

    async def _run_simple(
        self,
        prompt: str,
        context: Dict[str, Any],
    ) -> WorkerResult:
        """Run task with simple LLM call (no tools)."""
        if not self.llm_client:
            return WorkerResult(
                success=False,
                output="",
                error="No LLM client available",
                worker_id=self.worker_id,
            )
        
        try:
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ]
            
            response = self.llm_client.chat_completion(messages)
            
            if response.error:
                self.state = WorkerState.ERROR
                return WorkerResult(
                    success=False,
                    output="",
                    error=response.error,
                    worker_id=self.worker_id,
                )
            
            self.state = WorkerState.COMPLETED
            return WorkerResult(
                success=True,
                output=response.content,
                messages_exchanged=len(messages),
                tool_calls_made=0,
                worker_id=self.worker_id,
            )
            
        except Exception as e:
            self.state = WorkerState.ERROR
            return WorkerResult(
                success=False,
                output="",
                error=str(e),
                worker_id=self.worker_id,
            )

    def _build_prompt(self, task_description: str, context: Dict[str, Any]) -> str:
        """Build the full prompt with context."""
        parts = [task_description]
        
        # Add subtask context
        if "subtask_index" in context and "total_subtasks" in context:
            parts.insert(0, f"[Subtask {context['subtask_index'] + 1}/{context['total_subtasks']}]")
        
        # Add previous worker results if available
        if "previous_results" in context:
            parts.append("\n\nResults from previous workers:")
            for i, result in enumerate(context["previous_results"]):
                parts.append(f"\n--- Worker {i + 1} ---\n{result}")
        
        return "\n".join(parts)

    def stop(self) -> None:
        """Request the worker to stop."""
        self._stop_requested = True
        self.state = WorkerState.STOPPED
        if self._query_loop:
            self._query_loop.stop()

    def pause(self) -> None:
        """Pause the worker (if running)."""
        if self.state == WorkerState.RUNNING:
            self.state = WorkerState.PAUSED

    def resume(self) -> None:
        """Resume the worker (if paused)."""
        if self.state == WorkerState.PAUSED:
            self.state = WorkerState.RUNNING

    def get_state(self) -> WorkerState:
        """Get current worker state."""
        return self.state

    def get_message_history(self) -> List[Message]:
        """Get the worker's message history (for debugging)."""
        if self._query_loop:
            return self._query_loop.messages
        return []
