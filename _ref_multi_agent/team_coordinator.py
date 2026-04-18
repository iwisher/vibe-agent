"""Team coordinator for managing multiple worker agents."""

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional, TypeVar

from .worker import Worker, WorkerState, WorkerResult
from .synthesis import ResultSynthesizer, SynthesisStrategy
from .verification import ResultVerifier


class TaskStatus(Enum):
    """Status of a worker task."""
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class WorkerTask:
    """A task assigned to a worker."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 10
    timeout_seconds: float = 300.0
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[WorkerResult] = None
    assigned_worker: Optional[str] = None


@dataclass
class TeamConfig:
    """Configuration for a team of workers."""
    max_workers: int = 4
    enable_verification: bool = True
    verifier_is_independent: bool = True  # Verifier ≠ implementer rule
    synthesis_strategy: SynthesisStrategy = SynthesisStrategy.CONSENSUS
    task_timeout_seconds: float = 300.0
    enable_parallel: bool = True


@dataclass
class TeamResult:
    """Result of a team coordination operation."""
    success: bool
    final_answer: str
    worker_results: List[WorkerResult]
    synthesis_metadata: Dict[str, Any] = field(default_factory=dict)
    verification_passed: bool = True
    verification_feedback: Optional[str] = None


class TeamCoordinator:
    """Coordinates multiple worker agents for complex tasks.
    
    Implements the $team mode pattern:
    1. Spawns isolated workers with separate message contexts
    2. Distributes tasks to workers
    3. Collects and synthesizes results
    4. Independently verifies results (verifier ≠ implementer)
    """

    def __init__(
        self,
        config: Optional[TeamConfig] = None,
        llm_client=None,
        tool_system=None,
    ):
        self.config = config or TeamConfig()
        self.llm_client = llm_client
        self.tool_system = tool_system
        self._workers: Dict[str, Worker] = {}
        self._tasks: Dict[str, WorkerTask] = {}
        self._synthesizer = ResultSynthesizer(strategy=self.config.synthesis_strategy)
        self._verifier = ResultVerifier(llm_client=llm_client)
        self._running_tasks: Dict[str, asyncio.Task] = {}

    async def coordinate(
        self,
        main_task: str,
        subtasks: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> TeamResult:
        """Coordinate workers to complete a complex task.
        
        Args:
            main_task: The overall task description
            subtasks: Optional list of subtask descriptions
            context: Shared context for all workers
            
        Returns:
            TeamResult with synthesized output
        """
        context = context or {}
        
        # If no subtasks provided, create them by analyzing the main task
        if not subtasks:
            subtasks = await self._decompose_task(main_task, context)
        
        # Create worker tasks
        worker_tasks = [
            WorkerTask(
                description=subtask,
                context={
                    **context,
                    "main_task": main_task,
                    "subtask_index": i,
                    "total_subtasks": len(subtasks),
                },
            )
            for i, subtask in enumerate(subtasks)
        ]
        
        # Execute tasks
        if self.config.enable_parallel and len(worker_tasks) > 1:
            worker_results = await self._execute_parallel(worker_tasks)
        else:
            worker_results = await self._execute_sequential(worker_tasks)
        
        # Synthesize results
        synthesis = await self._synthesizer.synthesize(
            main_task=main_task,
            worker_results=worker_results,
        )
        
        # Independent verification (verifier ≠ implementer rule)
        verification_passed = True
        verification_feedback = None
        
        if self.config.enable_verification:
            verification = await self._verify_results(
                main_task=main_task,
                synthesis=synthesis,
                worker_results=worker_results,
            )
            verification_passed = verification.passed
            verification_feedback = verification.feedback
            
            # If verification failed, may need to re-run with feedback
            if not verification_passed and self.config.verifier_is_independent:
                # Retry with feedback
                synthesis = await self._synthesizer.synthesize_with_feedback(
                    main_task=main_task,
                    worker_results=worker_results,
                    verification_feedback=verification_feedback,
                )
        
        return TeamResult(
            success=all(r.success for r in worker_results) and verification_passed,
            final_answer=synthesis,
            worker_results=worker_results,
            synthesis_metadata={
                "strategy": self.config.synthesis_strategy.value,
                "worker_count": len(worker_results),
            },
            verification_passed=verification_passed,
            verification_feedback=verification_feedback,
        )

    async def spawn_worker(
        self,
        task: WorkerTask,
        worker_id: Optional[str] = None,
    ) -> Worker:
        """Spawn a new isolated worker.
        
        Each worker gets:
        - Isolated message context (no cross-contamination)
        - Own copy of tools
        - Separate state tracking
        """
        worker_id = worker_id or f"worker_{len(self._workers)}"
        
        worker = Worker(
            worker_id=worker_id,
            llm_client=self.llm_client,
            tool_system=self._create_isolated_tool_system(),
            max_iterations=task.max_iterations,
        )
        
        self._workers[worker_id] = worker
        task.assigned_worker = worker_id
        task.status = TaskStatus.PENDING
        self._tasks[task.id] = task
        
        return worker

    async def _execute_parallel(
        self,
        tasks: List[WorkerTask],
    ) -> List[WorkerResult]:
        """Execute tasks in parallel with worker pool."""
        # Limit concurrent workers
        semaphore = asyncio.Semaphore(self.config.max_workers)
        
        async def execute_with_limit(task: WorkerTask) -> WorkerResult:
            async with semaphore:
                return await self._execute_single(task)
        
        # Create and run all tasks
        coroutines = [execute_with_limit(task) for task in tasks]
        return await asyncio.gather(*coroutines, return_exceptions=True)

    async def _execute_sequential(
        self,
        tasks: List[WorkerTask],
    ) -> List[WorkerResult]:
        """Execute tasks one at a time."""
        results = []
        for task in tasks:
            result = await self._execute_single(task)
            results.append(result)
        return results

    async def _execute_single(self, task: WorkerTask) -> WorkerResult:
        """Execute a single task with a worker."""
        task.status = TaskStatus.RUNNING
        
        try:
            # Spawn or get worker
            if task.assigned_worker and task.assigned_worker in self._workers:
                worker = self._workers[task.assigned_worker]
            else:
                worker = await self.spawn_worker(task)
            
            # Run with timeout
            result = await asyncio.wait_for(
                worker.run_task(task.description, task.context),
                timeout=self.config.task_timeout_seconds,
            )
            
            task.result = result
            task.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            
            return result
            
        except asyncio.TimeoutError:
            task.status = TaskStatus.FAILED
            result = WorkerResult(
                success=False,
                output="",
                error=f"Task timed out after {self.config.task_timeout_seconds}s",
            )
            task.result = result
            return result
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            result = WorkerResult(
                success=False,
                output="",
                error=str(e),
            )
            task.result = result
            return result

    async def _verify_results(
        self,
        main_task: str,
        synthesis: str,
        worker_results: List[WorkerResult],
    ):
        """Verify results using an independent verifier."""
        # Get a worker that did NOT participate (verifier ≠ implementer)
        verifier_worker = None
        for worker_id, worker in self._workers.items():
            if not any(r.worker_id == worker_id for r in worker_results):
                verifier_worker = worker
                break
        
        # If all workers participated, create a fresh one for verification
        if verifier_worker is None:
            verifier_worker = Worker(
                worker_id=f"verifier_{uuid.uuid4()[:8]}",
                llm_client=self.llm_client,
                tool_system=self._create_isolated_tool_system(),
            )
        
        return await self._verifier.verify(
            original_task=main_task,
            proposed_solution=synthesis,
            worker_outputs=[r.output for r in worker_results],
            verifier_worker=verifier_worker,
        )

    async def _decompose_task(
        self,
        main_task: str,
        context: Dict[str, Any],
    ) -> List[str]:
        """Decompose a main task into subtasks."""
        # Simple heuristic-based decomposition
        # In a full implementation, this could use LLM for intelligent decomposition
        
        # Check for explicit subtask indicators
        if "\n- " in main_task or "\n1. " in main_task:
            # Already has list format, split it
            lines = main_task.strip().split("\n")
            subtasks = []
            for line in lines[1:]:  # Skip first line (main description)
                line = line.strip()
                if line.startswith("-") or line[0].isdigit():
                    subtasks.append(line.lstrip("- 1234567890.").strip())
            if subtasks:
                return subtasks
        
        # Default: treat as single task
        return [main_task]

    def _create_isolated_tool_system(self):
        """Create an isolated copy of the tool system for a worker."""
        if self.tool_system is None:
            return None
        
        # Create fresh tool system instance
        from ..tools.tool_system import ToolSystem
        isolated = ToolSystem()
        
        # Copy registered tools
        for tool_name in self.tool_system.list_tools():
            tool = self.tool_system.get_tool(tool_name)
            if tool:
                isolated.register_tool(tool)
        
        return isolated

    def stop_all(self) -> None:
        """Stop all running workers."""
        for worker in self._workers.values():
            worker.stop()
        
        for task in self._running_tasks.values():
            task.cancel()
        
        self._workers.clear()
        self._running_tasks.clear()

    def get_status(self) -> Dict[str, Any]:
        """Get current team status."""
        return {
            "active_workers": len(self._workers),
            "active_tasks": len(self._tasks),
            "tasks": {
                task_id: {
                    "status": task.status.value,
                    "worker": task.assigned_worker,
                }
                for task_id, task in self._tasks.items()
            },
        }
