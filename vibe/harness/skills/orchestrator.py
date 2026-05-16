"""Skill orchestrator — enables skills to await other skills and spawn sub-agents.

Replaces strictly sequential bash-step executors with:
- Skill-to-skill calls (await other skills within a skill)
- Sub-agent spawning (parallel skill execution)
- Dependency graph resolution for multi-skill workflows
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class SubTask:
    """A sub-task within a skill orchestration."""

    task_id: str
    skill_name: str
    variables: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None


@dataclass
class OrchestrationResult:
    """Result of a skill orchestration."""

    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    execution_order: list[str] = field(default_factory=list)


class SkillOrchestrator:
    """Orchestrate multi-skill workflows with dependency resolution.

    Enables:
    - skills to call other skills (skill composition)
    - parallel sub-agent spawning via asyncio.gather
    - DAG-based dependency resolution
    """

    def __init__(
        self,
        skill_registry: dict[str, Any],
        executor_factory: Callable[[], Any],
        max_concurrency: int = 4,
    ) -> None:
        self.skill_registry = skill_registry
        self.executor_factory = executor_factory
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_skill(
        self,
        skill_name: str,
        variables: dict[str, Any] | None = None,
    ) -> Any:
        """Run a single skill by name (allows skill-to-skill calls)."""
        skill = self.skill_registry.get(skill_name)
        if skill is None:
            raise ValueError(f"Skill not found: {skill_name}")

        executor = self.executor_factory()
        async with self._semaphore:
            # Assume executor has an async execute method or wrap sync
            if hasattr(executor, "execute_async"):
                return await executor.execute_async(skill, variables or {})
            else:
                # Run sync executor in thread pool
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, executor.execute, skill, variables or {}
                )

    async def run_parallel(
        self,
        skill_calls: list[tuple[str, dict[str, Any]]],
    ) -> list[Any]:
        """Run multiple skills in parallel (sub-agent spawning).

        Args:
            skill_calls: List of (skill_name, variables) tuples

        Returns:
            List of results in same order as inputs
        """
        tasks = [
            self.run_skill(name, vars)
            for name, vars in skill_calls
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def _resolve_dag(self, tasks: list[SubTask]) -> list[list[SubTask]]:
        """Resolve task dependencies into execution waves.

        Returns list of waves, where each wave contains tasks that can
        run in parallel (all dependencies satisfied).
        """
        pending = {t.task_id: t for t in tasks}
        completed: set[str] = set()
        waves: list[list[SubTask]] = []

        while pending:
            wave = [
                t for t in pending.values()
                if all(dep in completed for dep in t.dependencies)
            ]
            if not wave:
                # Circular dependency or missing dependency
                raise ValueError(
                    f"Cannot resolve dependencies for tasks: {list(pending.keys())}"
                )

            waves.append(wave)
            for t in wave:
                completed.add(t.task_id)
                del pending[t.task_id]

        return waves

    async def run_dag(self, tasks: list[SubTask]) -> OrchestrationResult:
        """Execute a DAG of sub-tasks with dependency resolution.

        Args:
            tasks: List of SubTask definitions with dependencies

        Returns:
            OrchestrationResult with all outputs
        """
        result = OrchestrationResult(success=True)
        task_map = {t.task_id: t for t in tasks}

        try:
            waves = self._resolve_dag(tasks)
        except ValueError as e:
            return OrchestrationResult(
                success=False,
                errors={"__dag__": str(e)},
            )

        for wave in waves:
            # Run all tasks in wave concurrently
            async def run_with_id(task: SubTask) -> tuple[str, Any, str | None]:
                try:
                    task.status = TaskStatus.RUNNING
                    output = await self.run_skill(
                        task.skill_name, task.variables
                    )
                    task.status = TaskStatus.COMPLETED
                    task.result = output
                    return task.task_id, output, None
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    return task.task_id, None, str(e)

            wave_results = await asyncio.gather(*[
                run_with_id(t) for t in wave
            ])

            for task_id, output, error in wave_results:
                if error:
                    result.errors[task_id] = error
                    result.success = False
                else:
                    result.outputs[task_id] = output
                result.execution_order.append(task_id)

        return result

    async def call_skill_from_skill(
        self,
        caller_skill: str,
        callee_skill: str,
        variables: dict[str, Any] | None = None,
    ) -> Any:
        """Allow one skill to call another skill (skill composition).

        This is the core mechanism for skills awaiting other skills.
        """
        logger.debug(f"Skill '{caller_skill}' calling skill '{callee_skill}'")
        return await self.run_skill(callee_skill, variables)
