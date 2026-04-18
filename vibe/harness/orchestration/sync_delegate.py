"""Synchronous delegate for isolated subagent execution."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from vibe.core.model_gateway import LLMClient
from vibe.core.query_loop import QueryLoop
from vibe.tools.tool_system import ToolSystem


@dataclass
class DelegateTask:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 20
    timeout_seconds: float = 300.0


@dataclass
class DelegateResult:
    task_id: str
    success: bool
    output: str
    error: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None


class SyncDelegate:
    """Runs up to N isolated subagents in parallel and returns summaries."""

    def __init__(
        self,
        llm_client_factory: Callable[[], LLMClient],
        tool_system_factory: Callable[[], ToolSystem],
        max_workers: int = 3,
    ):
        self.llm_factory = llm_client_factory
        self.tool_factory = tool_system_factory
        self.max_workers = max_workers

    async def run(
        self,
        tasks: List[DelegateTask],
    ) -> List[DelegateResult]:
        semaphore = asyncio.Semaphore(self.max_workers)

        async def _run_one(task: DelegateTask) -> DelegateResult:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self._execute_task(task),
                        timeout=task.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    return DelegateResult(
                        task_id=task.id,
                        success=False,
                        output="",
                        error=f"Task timed out after {task.timeout_seconds}s",
                    )

        coros = [_run_one(t) for t in tasks]
        return await asyncio.gather(*coros)

    async def _execute_task(self, task: DelegateTask) -> DelegateResult:
        llm = self.llm_factory()
        tools = self.tool_factory()
        loop = QueryLoop(llm_client=llm, tool_system=tools, max_iterations=task.max_iterations)

        prompt = task.description
        if task.context:
            prompt += f"\n\nContext: {task.context}"

        outputs = []
        tool_results = []
        try:
            async for result in loop.run(initial_query=prompt):
                if result.error:
                    return DelegateResult(
                        task_id=task.id,
                        success=False,
                        output="",
                        error=str(result.error),
                    )
                outputs.append(result.response)
                if result.tool_results:
                    for tr in result.tool_results:
                        tool_results.append(tr.content if tr.success else tr.error)
        except Exception as e:
            return DelegateResult(
                task_id=task.id,
                success=False,
                output="",
                error=str(e),
            )
        finally:
            await llm.close()

        full_output = "\n".join(outputs + tool_results).strip()
        return DelegateResult(
            task_id=task.id,
            success=True,
            output=full_output,
        )
