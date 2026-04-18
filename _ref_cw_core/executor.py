"""Task executor with Celery for async execution."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded, MaxRetriesExceededError

from .models import Task, TaskStatus, StepStatus, TaskEvent, TaskStep
from .database import TaskDatabase, SingleWriterDatabase
from ..config import get_config
from .planner import TaskPlanner
from .llm import LLMClient


# Event callbacks for real-time updates (CLI/WebSocket)
_event_callbacks: List[Callable[[TaskEvent], None]] = []


def register_event_callback(callback: Callable[[TaskEvent], None]) -> None:
    """Register a callback for task events."""
    _event_callbacks.append(callback)


def unregister_event_callback(callback: Callable[[TaskEvent], None]) -> None:
    """Unregister a callback."""
    if callback in _event_callbacks:
        _event_callbacks.remove(callback)


def emit_event(event: TaskEvent) -> None:
    """Emit event to all registered callbacks."""
    for callback in _event_callbacks:
        try:
            callback(event)
        except Exception as e:
            # Log callback errors but don't let them break execution
            logging.getLogger(__name__).warning(f"Event callback failed: {e}")


class TaskExecutor:
    """Executes tasks using the plugin system."""

    def __init__(
        self,
        db: Any,
        llm: LLMClient,
        plugin_registry: Optional[Any] = None,
    ):
        self.db = db
        self.llm = llm
        self.plugin_registry = plugin_registry
        self._running_tasks: Dict[str, Task] = {}

    async def execute_step(
        self,
        task: Task,
        step: TaskStep,
    ) -> Any:
        """Execute a single step using appropriate plugin."""
        if not self.plugin_registry:
            # Fallback: use LLM directly
            return await self._execute_with_llm(task, step)

        if not step.plugin:
            # No plugin specified, use LLM
            return await self._execute_with_llm(task, step)

        plugin = self.plugin_registry.get_plugin(step.plugin)
        if not plugin:
            raise ValueError(f"Plugin '{step.plugin}' not found")

        # Execute the plugin action
        result = await plugin.execute(step.action, step.params)
        return result

    async def _execute_with_llm(self, task: Task, step: TaskStep) -> str:
        """Execute step using LLM directly."""
        from .llm import Message

        messages = [
            Message(
                role="system",
                content="You are a helpful assistant executing a task step.",
            ),
            Message(
                role="user",
                content=f"Task: {task.description}\nStep: {step.description}\nParams: {step.params}",
            ),
        ]

        return await self.llm.chat(messages)

    async def execute_task(self, task_id: str) -> Task:
        """Execute a task plan step by step."""
        # Handle both sync and async database
        if isinstance(self.db, SingleWriterDatabase):
            task = await self.db.get_task(task_id)
        else:
            task = self.db.get_task(task_id)

        if not task:
            raise ValueError(f"Task {task_id} not found")

        if not task.plan:
            raise ValueError(f"Task {task_id} has no plan")

        self._running_tasks[task_id] = task

        try:
            # Update status to running
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc)

            if isinstance(self.db, SingleWriterDatabase):
                await self.db.save_task(task)
            else:
                self.db.save_task(task)

            emit_event(TaskEvent(
                task_id=task_id,
                event_type="status_change",
                data={"status": TaskStatus.RUNNING},
            ))

            # Execute each step
            for step in task.plan.steps:
                if step.status == StepStatus.COMPLETED:
                    continue

                # Check dependencies
                deps_satisfied = all(
                    any(s.id == dep and s.status == StepStatus.COMPLETED
                        for s in task.plan.steps)
                    for dep in step.depends_on
                )

                if not deps_satisfied:
                    step.status = StepStatus.PENDING
                    continue

                # Execute step
                step.status = StepStatus.RUNNING
                step.started_at = datetime.now(timezone.utc)

                if isinstance(self.db, SingleWriterDatabase):
                    await self.db.save_task(task)
                else:
                    self.db.save_task(task)

                emit_event(TaskEvent(
                    task_id=task_id,
                    event_type="step_start",
                    data={"step_id": step.id, "description": step.description},
                ))

                try:
                    result = await self.execute_step(task, step)
                    step.result = result
                    step.status = StepStatus.COMPLETED
                    step.completed_at = datetime.now(timezone.utc)

                    emit_event(TaskEvent(
                        task_id=task_id,
                        event_type="step_complete",
                        data={
                            "step_id": step.id,
                            "status": "completed",
                            "result_preview": str(result)[:200] if result else None,
                        },
                    ))

                except Exception as e:
                    step.status = StepStatus.FAILED
                    step.error = str(e)

                    emit_event(TaskEvent(
                        task_id=task_id,
                        event_type="step_error",
                        data={"step_id": step.id, "error": str(e)},
                    ))

                    # Mark task as failed
                    task.status = TaskStatus.FAILED
                    task.error_message = f"Step {step.id} failed: {e}"

                    if isinstance(self.db, SingleWriterDatabase):
                        await self.db.save_task(task)
                    else:
                        self.db.save_task(task)
                    return task

                if isinstance(self.db, SingleWriterDatabase):
                    await self.db.save_task(task)
                else:
                    self.db.save_task(task)

                # Emit progress update
                completed, total = task.plan.get_progress()
                emit_event(TaskEvent(
                    task_id=task_id,
                    event_type="progress",
                    data={
                        "completed": completed,
                        "total": total,
                        "percent": task.plan.percent_complete,
                    },
                ))

            # All steps completed
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)

            if isinstance(self.db, SingleWriterDatabase):
                await self.db.save_task(task)
            else:
                self.db.save_task(task)

            emit_event(TaskEvent(
                task_id=task_id,
                event_type="status_change",
                data={"status": TaskStatus.COMPLETED},
            ))

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)

            if isinstance(self.db, SingleWriterDatabase):
                await self.db.save_task(task)
            else:
                self.db.save_task(task)

            emit_event(TaskEvent(
                task_id=task_id,
                event_type="error",
                data={"error": str(e)},
            ))

        finally:
            if task_id in self._running_tasks:
                del self._running_tasks[task_id]

        return task

    async def pause_task(self, task_id: str) -> Task:
        """Pause a running task."""
        if isinstance(self.db, SingleWriterDatabase):
            task = await self.db.get_task(task_id)
        else:
            task = self.db.get_task(task_id)

        if task and task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.PAUSED

            if isinstance(self.db, SingleWriterDatabase):
                await self.db.save_task(task)
            else:
                self.db.save_task(task)

            emit_event(TaskEvent(
                task_id=task_id,
                event_type="status_change",
                data={"status": TaskStatus.PAUSED},
            ))
        return task

    async def resume_task(self, task_id: str) -> Task:
        """Resume a paused task."""
        if isinstance(self.db, SingleWriterDatabase):
            task = await self.db.get_task(task_id)
        else:
            task = self.db.get_task(task_id)

        if task and task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.RUNNING

            if isinstance(self.db, SingleWriterDatabase):
                await self.db.save_task(task)
            else:
                self.db.save_task(task)

            return await self.execute_task(task_id)
        return task

    async def cancel_task(self, task_id: str) -> Task:
        """Cancel a task."""
        if isinstance(self.db, SingleWriterDatabase):
            task = await self.db.get_task(task_id)
        else:
            task = self.db.get_task(task_id)

        if task:
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now(timezone.utc)

            if isinstance(self.db, SingleWriterDatabase):
                await self.db.save_task(task)
            else:
                self.db.save_task(task)

            emit_event(TaskEvent(
                task_id=task_id,
                event_type="status_change",
                data={"status": TaskStatus.CANCELLED},
            ))
        return task

    def get_running_tasks(self) -> List[Task]:
        """Get currently running tasks."""
        return list(self._running_tasks.values())


# Celery task wrapper using correct async pattern
async def _execute_task_async(task_id: str) -> Dict[str, Any]:
    """
    Actual async implementation of task execution.

    This function contains the real execution logic and is designed to be
    called from the Celery task wrapper using async_to_sync().
    """
    config = get_config()

    # Create database instance based on feature flag
    if config.features.use_single_writer:
        db = SingleWriterDatabase()
        await db.initialize()
    else:
        db = TaskDatabase()

    llm = LLMClient()

    # Import plugin registry if available
    try:
        from ..plugins.registry import PluginRegistry
        registry = PluginRegistry()
        registry.load_plugins()
    except Exception:
        registry = None

    executor = TaskExecutor(db, llm, registry)

    try:
        result_task = await executor.execute_task(task_id)
        return {
            "task_id": task_id,
            "status": result_task.status.value,
            "success": result_task.status == TaskStatus.COMPLETED,
        }
    finally:
        # Clean up async database if used
        # Use hasattr check to handle both real class and mocks in tests
        if hasattr(db, 'close') and asyncio.iscoroutinefunction(db.close):
            await db.close()


# Celery task wrapper using correct async pattern with proper event loop handling
# The key issue is that Celery runs in a sync context but we need to execute async code.
# We use a thread-local event loop approach that is compatible with both sync and async contexts.

def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """Get the current event loop or create a new one if needed."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        # No event loop in current thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run_async_in_sync(coro) -> Any:
    """Run an async coroutine in a sync context, handling loop conflicts."""
    loop = _get_or_create_event_loop()

    # Check if we're already in an event loop (nested async context)
    try:
        # Try to get the running loop - if this succeeds, we're in an async context
        running_loop = asyncio.get_running_loop()
        if running_loop.is_running():
            # We're in a running loop, use run_coroutine_threadsafe
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
    except RuntimeError:
        # No running loop, we can use run_until_complete
        pass

    return loop.run_until_complete(coro)


# Try to use asgiref's async_to_sync for proper Celery async handling (preferred method)
try:
    from asgiref.sync import async_to_sync
    HAS_ASGIREF = True
except ImportError:
    HAS_ASGIREF = False
    import warnings
    warnings.warn(
        "asgiref not installed. Using fallback async handling. "
        "Install with: pip install asgiref for better performance",
        RuntimeWarning
    )


@shared_task(bind=True, max_retries=3)
def execute_task_async(self, task_id: str) -> Dict[str, Any]:
    """
    Celery task to execute a ClaudeWorker task asynchronously.

    Uses proper async handling to avoid event loop conflicts.
    Prefers asgiref's async_to_sync() when available for optimal performance.
    """
    try:
        if HAS_ASGIREF:
            # Use asgiref for proper async handling
            return async_to_sync(_execute_task_async)(task_id)
        else:
            # Fallback: use custom async handling
            return _run_async_in_sync(_execute_task_async(task_id))

    except SoftTimeLimitExceeded:
        # Retry after 60 seconds if time limit exceeded
        raise self.retry(countdown=60)

    except RuntimeError as exc:
        # Handle "Event loop is closed" errors specifically
        if "loop is closed" in str(exc).lower() or "event loop" in str(exc).lower():
            # Force create a new loop and retry once
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                if HAS_ASGIREF:
                    return async_to_sync(_execute_task_async)(task_id)
                else:
                    return loop.run_until_complete(_execute_task_async(task_id))
            finally:
                if not HAS_ASGIREF:
                    loop.close()
        # Re-raise other RuntimeErrors
        raise self.retry(exc=exc, countdown=60)

    except Exception as exc:
        # Retry on other failures
        raise self.retry(exc=exc, countdown=60)


async def submit_task(task_id: str) -> str:
    """Submit a task for async execution via Celery."""
    result = execute_task_async.delay(task_id)
    return result.id
