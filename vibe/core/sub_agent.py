"""Sub-agent runner for /bg and /btw commands."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibe.core.query_loop import QueryLoop
    from vibe.core.query_loop_factory import QueryLoopFactory


@dataclass
class SubAgentResult:
    """Summary of a completed sub-agent run."""

    success: bool
    response: str = ""
    error: str | None = None
    results: list[Any] = field(default_factory=list)


class SubAgentRunner:
    """Runs a QueryLoop in a background asyncio.Task with isolated state."""

    def __init__(self, factory: "QueryLoopFactory", session_id: str) -> None:
        self.factory = factory
        self.session_id = session_id
        self.loop: "QueryLoop" | None = None
        self.task: asyncio.Task | None = None
        self.results: list[Any] = []
        self._completed = asyncio.Event()
        self._started_at: float = 0.0

    async def start(self, query: str) -> None:
        """Start background processing of a query."""
        self.loop = self.factory.create()
        self._started_at = asyncio.get_event_loop().time()
        self.task = asyncio.create_task(self._run(query))

    async def _run(self, query: str) -> None:
        """Drive the sub-agent loop. Never raises."""
        try:
            self.loop.add_user_message(query)
            async for result in self.loop.run():
                self.results.append(result)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            from vibe.core.query_loop import QueryResult

            self.results.append(QueryResult(error=e, state=getattr(self.loop, "_state", None)))
        finally:
            self._completed.set()

    async def wait(self, timeout: float | None = None) -> list[Any]:
        """Wait for completion. Returns all results."""
        await asyncio.wait_for(self._completed.wait(), timeout=timeout)
        return self.results

    def is_done(self) -> bool:
        return self._completed.is_set()

    def stop(self) -> None:
        """Cancel the background task and stop the query loop."""
        if self.loop is not None:
            self.loop.stop()
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def extract_final_response(self) -> str:
        """Extract the assistant's final response from the result stream."""
        for r in reversed(self.results):
            if (
                hasattr(r, "response")
                and r.response
                and not getattr(r, "is_status", False)
                and not getattr(r, "is_stream_chunk", False)
            ):
                return r.response
        return ""
