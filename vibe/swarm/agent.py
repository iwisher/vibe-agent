"""SubAgent — specialized agent wrapper with role configs and scratchpad.

Each SubAgent has a role (RESEARCH, CODING, CRITIC, PLANNER) with
specialized system prompts, tool sets, and isolated scratchpad state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from vibe.swarm.protocol import AgentMessage, MessageBus, MessageType


class SubAgentRole(Enum):
    RESEARCH = auto()
    CODING = auto()
    CRITIC = auto()
    PLANNER = auto()


class AgentLifecycle(Enum):
    SPAWNED = auto()
    ACTIVE = auto()
    IDLE = auto()
    TERMINATED = auto()


ROLE_PROMPTS: dict[SubAgentRole, str] = {
    SubAgentRole.RESEARCH: (
        "You are a Research Agent. Your job is to gather information, "
        "search the web, read documentation, and compile findings. "
        "Be thorough and cite sources. Report findings as structured data."
    ),
    SubAgentRole.CODING: (
        "You are a Coding Agent. Your job is to write, refactor, and debug code. "
        "Follow best practices, write tests, and ensure code quality. "
        "Use the provided tools to read and write files safely."
    ),
    SubAgentRole.CRITIC: (
        "You are a Critic Agent. Your job is to review code and findings "
        "for correctness, security, and quality. Be thorough but constructive. "
        "Flag issues with severity levels and suggest fixes."
    ),
    SubAgentRole.PLANNER: (
        "You are a Planner Agent. Your job is to break down complex tasks "
        "into actionable sub-tasks with clear dependencies. "
        "Output a structured task DAG with prerequisites."
    ),
}


@dataclass
class Scratchpad:
    """Isolated working memory for a sub-agent."""

    notes: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    def add_artifact(self, name: str, data: Any) -> None:
        self.artifacts[name] = data

    def to_dict(self) -> dict[str, Any]:
        return {"notes": self.notes, "artifacts": self.artifacts}


@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent."""

    role: SubAgentRole
    model: str = "gpt-4o-mini"
    max_iterations: int = 5
    timeout_seconds: float = 120.0
    tools: list[str] = field(default_factory=list)


class SubAgent:
    """A specialized sub-agent that runs in its own asyncio task.

    Lifecycle: SPAWNED → ACTIVE → IDLE → TERMINATED
    """

    def __init__(
        self,
        agent_id: str,
        config: SubAgentConfig,
        bus: MessageBus,
        shared_wiki: Any | None = None,
    ):
        self.agent_id = agent_id
        self.config = config
        self.bus = bus
        self.shared_wiki = shared_wiki
        self.scratchpad = Scratchpad()
        self.lifecycle = AgentLifecycle.SPAWNED
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def system_prompt(self) -> str:
        return ROLE_PROMPTS.get(self.config.role, "You are a helpful agent.")

    async def start(self) -> None:
        """Start the agent's message processing loop."""
        self.lifecycle = AgentLifecycle.ACTIVE
        self._ready_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        await self._ready_event.wait()  # Wait for loop to start listening

    async def _run_loop(self) -> None:
        """Main message processing loop."""
        queue = await self.bus.register_agent(self.agent_id)
        self._ready_event.set()  # Signal that we're ready
        try:
            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                if msg.msg_type == MessageType.DONE:
                    break

                await self._handle_message(msg)
        except asyncio.CancelledError:
            pass
        finally:
            self.lifecycle = AgentLifecycle.IDLE

    async def _handle_message(self, msg: AgentMessage) -> None:
        """Process an incoming message."""
        if msg.msg_type == MessageType.TASK:
            await self._execute_task(msg)
        elif msg.msg_type == MessageType.QUESTION:
            # Store in scratchpad, maybe reply
            self.scratchpad.add_note(f"Q: {msg.content}")
        elif msg.msg_type == MessageType.CRITIQUE:
            self.scratchpad.add_note(f"Critique: {msg.content}")
        elif msg.msg_type == MessageType.BROADCAST:
            self.scratchpad.add_note(f"Broadcast: {msg.content}")

    async def _execute_task(self, msg: AgentMessage) -> None:
        """Execute a task and report results."""
        self.scratchpad.add_note(f"Task: {msg.content}")

        # Simulate work (replace with actual QueryLoop execution)
        result = f"[{self.config.role.name}] Completed: {msg.content}"

        await self.bus.send(
            msg_type=MessageType.RESULT,
            sender=self.agent_id,
            recipient="orchestrator",
            content=result,
            correlation_id=msg.correlation_id,
            metadata={
                "role": self.config.role.name,
                "scratchpad": self.scratchpad.to_dict(),
            },
        )

    async def stop(self) -> None:
        """Gracefully stop the agent."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.lifecycle = AgentLifecycle.TERMINATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.config.role.name,
            "lifecycle": self.lifecycle.name,
            "scratchpad": self.scratchpad.to_dict(),
        }
