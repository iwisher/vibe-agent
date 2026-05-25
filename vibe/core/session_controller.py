"""SessionController orchestrates main session + sub-agents for queue/bg/btw."""

import asyncio
from dataclasses import dataclass
from typing import Any

from vibe.core.conversation_queue import ConversationQueue, QueuedMessage, SteerCommand
from vibe.core.query_loop import QueryLoop, QueryResult
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.core.sub_agent import SubAgentRunner


@dataclass
class OutputEvent:
    source: str  # "main" | "bg_<id>" | "btw"
    result: QueryResult


class SessionController:
    """Orchestrates main session + /bg and /btw sub-agents."""

    def __init__(self, factory: QueryLoopFactory) -> None:
        self.factory = factory
        self.main_loop: QueryLoop = factory.create()
        self.queue = ConversationQueue()
        self.main_task: asyncio.Task | None = None
        self.bg_agents: dict[str, SubAgentRunner] = {}
        self.btw_agent: SubAgentRunner | None = None
        self.output_queue: asyncio.Queue[OutputEvent] = asyncio.Queue()
        self._shutting_down = False

    async def start(self) -> None:
        """Start the main session worker."""
        self.main_task = asyncio.create_task(self._main_worker())

    async def _main_worker(self) -> None:
        """Process queued messages for the main session."""
        while not self._shutting_down:
            try:
                item = await self.queue.next_item()
                if item is None:
                    continue

                if isinstance(item, SteerCommand):
                    await self._handle_steer(item)
                    continue

                # Normal message (including btw_result)
                self.main_loop.add_user_message(item.content)
                async for result in self.main_loop.run():
                    await self.output_queue.put(OutputEvent("main", result))
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.output_queue.put(
                    OutputEvent("main", QueryResult(error=e, state=self.main_loop.state))
                )

    async def _handle_steer(self, cmd: SteerCommand) -> None:
        """Handle a steer command."""
        if cmd.type == "stop":
            self.main_loop.stop()
        elif cmd.type == "inject_context":
            from vibe.core.query_loop import Message

            self.main_loop.messages.append(Message(role="system", content=str(cmd.payload)))
        elif cmd.type == "switch_model":
            self.main_loop.set_model(str(cmd.payload))

    async def send_bg(self, query: str) -> str:
        """Start a /bg sub-agent. Returns agent_id."""
        agent_id = f"bg_{len(self.bg_agents)}"
        runner = SubAgentRunner(self.factory, agent_id)
        await runner.start(query)
        self.bg_agents[agent_id] = runner
        asyncio.create_task(self._bg_monitor(agent_id, runner))
        return agent_id

    async def _bg_monitor(self, agent_id: str, runner: SubAgentRunner) -> None:
        """Stream bg agent output to output_queue."""
        last_len = 0
        try:
            while not runner.is_done():
                await asyncio.sleep(0.1)
                new_results = runner.results[last_len:]
                for r in new_results:
                    await self.output_queue.put(OutputEvent(agent_id, r))
                last_len = len(runner.results)
            # Final flush
            for r in runner.results[last_len:]:
                await self.output_queue.put(OutputEvent(agent_id, r))
        except asyncio.CancelledError:
            pass

    async def send_btw(self, query: str) -> str:
        """Start a /btw sub-agent. When done, inject result into main queue."""
        runner = SubAgentRunner(self.factory, "btw")
        await runner.start(query)
        self.btw_agent = runner
        asyncio.create_task(self._btw_completion_monitor(runner))
        return "btw"

    async def _btw_completion_monitor(self, runner: SubAgentRunner) -> None:
        """Wait for btw to finish, enqueue summary into main queue."""
        try:
            await runner.wait(timeout=300.0)
            final = runner.extract_final_response()
            summary = f"[btw result] {final}" if final else "[btw completed with no response]"
            await self.queue.enqueue(summary, source="btw_result")
        except asyncio.TimeoutError:
            runner.stop()
            await self.queue.enqueue("[btw timed out after 5 minutes]", source="btw_result")
        except Exception as e:
            await self.queue.enqueue(f"[btw error: {e}]", source="btw_result")

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        self._shutting_down = True
        self.main_loop.stop()
        # Cancel and await bg agents
        for runner in self.bg_agents.values():
            runner.stop()
            if runner.task and not runner.task.done():
                try:
                    await runner.task
                except asyncio.CancelledError:
                    pass
        if self.btw_agent:
            self.btw_agent.stop()
            if self.btw_agent.task and not self.btw_agent.task.done():
                try:
                    await self.btw_agent.task
                except asyncio.CancelledError:
                    pass
        if self.main_task and not self.main_task.done():
            self.main_task.cancel()
            try:
                await self.main_task
            except asyncio.CancelledError:
                pass

    def list_bg_agents(self) -> list[dict[str, Any]]:
        """List running background agents."""
        return [
            {"id": aid, "done": r.is_done(), "result_count": len(r.results)}
            for aid, r in self.bg_agents.items()
        ]
