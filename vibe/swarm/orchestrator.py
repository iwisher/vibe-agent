"""SwarmOrchestrator — DAG-based task scheduler with sub-agent spawning.

Decomposes complex tasks into a DAG of sub-tasks, spawns role-based
sub-agents, and aggregates results. Single authoritative owner for wiki updates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from vibe.swarm.agent import SubAgent, SubAgentConfig, SubAgentRole
from vibe.swarm.protocol import AgentMessage, MessageBus, MessageType
from vibe.swarm.shared_wiki import SharedWiki, WikiUpdateRequest


@dataclass
class TaskNode:
    """A node in the task DAG."""

    node_id: str
    description: str
    role: SubAgentRole
    prerequisites: list[str] = field(default_factory=list)
    completed: bool = False
    result: str | None = None
    error: str | None = None


@dataclass
class TaskDAG:
    """Directed Acyclic Graph of tasks."""

    nodes: dict[str, TaskNode] = field(default_factory=dict)

    def add_node(self, node: TaskNode) -> None:
        self.nodes[node.node_id] = node

    def get_ready_nodes(self) -> list[TaskNode]:
        """Return nodes whose prerequisites are all completed."""
        ready = []
        for node in self.nodes.values():
            if node.completed or node.error:
                continue
            prereqs_met = all(
                self.nodes.get(p, TaskNode(p, "", SubAgentRole.PLANNER)).completed
                for p in node.prerequisites
            )
            if prereqs_met:
                ready.append(node)
        return ready

    def is_complete(self) -> bool:
        return all(n.completed or n.error for n in self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {
                nid: {
                    "node_id": n.node_id,
                    "description": n.description,
                    "role": n.role.name,
                    "prerequisites": n.prerequisites,
                    "completed": n.completed,
                    "result": n.result,
                    "error": n.error,
                }
                for nid, n in self.nodes.items()
            }
        }


@dataclass
class SwarmResult:
    """Result of a swarm execution."""

    task: str
    dag: TaskDAG
    agent_outputs: dict[str, list[AgentMessage]] = field(default_factory=dict)
    final_synthesis: str | None = None
    duration_seconds: float = 0.0
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "dag": self.dag.to_dict(),
            "agent_outputs": {
                aid: [{"type": m.msg_type.name, "content": m.content} for m in msgs]
                for aid, msgs in self.agent_outputs.items()
            },
            "final_synthesis": self.final_synthesis,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
        }


class SwarmOrchestrator:
    """Main coordinator for multi-agent swarm execution.

    Usage:
        orchestrator = SwarmOrchestrator(wiki=shared_wiki)
        result = await orchestrator.run("Build a REST API with auth")
    """

    def __init__(
        self,
        wiki: SharedWiki | None = None,
        max_concurrency: int = 4,
        max_agents: int = 8,
    ):
        self.wiki = wiki or SharedWiki()
        self.bus = MessageBus()
        self.max_concurrency = max_concurrency
        self.max_agents = max_agents
        self._agents: dict[str, SubAgent] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._wiki_update_queue: asyncio.Queue[WikiUpdateRequest] = asyncio.Queue()

    async def run(self, task: str) -> SwarmResult:
        """Execute a complex task via swarm orchestration."""
        start_time = asyncio.get_event_loop().time()

        # Start wiki update processor
        wiki_task = asyncio.create_task(self.process_wiki_updates())

        try:
            # Phase 1: Decompose into DAG
            dag = await self._decompose_task(task)

            # Phase 2: Execute DAG
            await self._execute_dag(dag)

            # Phase 3: Synthesize results
            synthesis = await self._synthesize_results(dag, task)

            duration = asyncio.get_event_loop().time() - start_time

            # Collect agent outputs
            outputs: dict[str, list[AgentMessage]] = {}
            for agent_id, agent in self._agents.items():
                outputs[agent_id] = []

            return SwarmResult(
                task=task,
                dag=dag,
                agent_outputs=outputs,
                final_synthesis=synthesis,
                duration_seconds=duration,
                success=all(n.completed for n in dag.nodes.values()),
            )
        finally:
            wiki_task.cancel()
            try:
                await wiki_task
            except asyncio.CancelledError:
                pass

    async def _decompose_task(self, task: str) -> TaskDAG:
        """Decompose a task into a DAG of sub-tasks.

        For now, uses a simple heuristic. In production, this would use
        an LLM-based planner agent.
        """
        dag = TaskDAG()

        # Simple decomposition: research → code → critique
        dag.add_node(
            TaskNode(
                node_id="research",
                description=f"Research requirements for: {task}",
                role=SubAgentRole.RESEARCH,
            )
        )
        dag.add_node(
            TaskNode(
                node_id="coding",
                description=f"Implement solution for: {task}",
                role=SubAgentRole.CODING,
                prerequisites=["research"],
            )
        )
        dag.add_node(
            TaskNode(
                node_id="critique",
                description=f"Review implementation for: {task}",
                role=SubAgentRole.CRITIC,
                prerequisites=["coding"],
            )
        )

        return dag

    async def _execute_dag(self, dag: TaskDAG) -> None:
        """Execute all tasks in the DAG respecting dependencies."""
        pending_tasks: dict[str, asyncio.Task[None]] = {}

        while not dag.is_complete():
            ready = dag.get_ready_nodes()

            for node in ready:
                if node.node_id in pending_tasks:
                    continue
                if len(self._agents) >= self.max_agents:
                    break

                task = asyncio.create_task(self._run_node(node, dag))
                pending_tasks[node.node_id] = task

            if not pending_tasks:
                break

            # Wait for at least one task to complete
            done, _ = await asyncio.wait(
                pending_tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                # Find which node completed
                for node_id, t in list(pending_tasks.items()):
                    if t is task:
                        del pending_tasks[node_id]
                        try:
                            await t
                        except Exception as e:
                            dag.nodes[node_id].error = str(e)
                        break

        # Cancel any remaining tasks
        for task in pending_tasks.values():
            task.cancel()

        # Stop all agents
        for agent in self._agents.values():
            await agent.stop()
        self._agents.clear()

    async def _run_node(self, node: TaskNode, dag: TaskDAG) -> None:
        """Execute a single DAG node with a sub-agent."""
        agent_id = f"{node.role.name.lower()}-{node.node_id}"

        async with self._semaphore:
            config = SubAgentConfig(role=node.role)
            agent = SubAgent(
                agent_id=agent_id,
                config=config,
                bus=self.bus,
                shared_wiki=self.wiki,
            )
            self._agents[agent_id] = agent
            await agent.start()
            await asyncio.sleep(0.1)  # Let agent start its loop

            # Register orchestrator queue once (not per node)
            if "orchestrator" not in self.bus._agent_queues:
                await self.bus.register_agent("orchestrator")

            # Send task to agent
            await self.bus.send(
                msg_type=MessageType.TASK,
                sender="orchestrator",
                recipient=agent_id,
                content=node.description,
                correlation_id=node.node_id,
            )

            # Wait for result
            queue = self.bus._agent_queues["orchestrator"]
            timeout = config.timeout_seconds

            try:
                while True:
                    msg = await asyncio.wait_for(queue.get(), timeout=timeout)
                    if msg.correlation_id == node.node_id and msg.msg_type == MessageType.RESULT:
                        node.result = msg.content
                        node.completed = True
                        break
                    elif msg.msg_type == MessageType.ERROR:
                        node.error = msg.content
                        break
            except asyncio.TimeoutError:
                node.error = f"Timeout after {timeout}s"
            finally:
                await agent.stop()
                del self._agents[agent_id]

    async def _synthesize_results(self, dag: TaskDAG, original_task: str) -> str:
        """Synthesize all sub-agent outputs into a final response."""
        parts = [f"# Swarm Execution Results: {original_task}\n"]

        for node in dag.nodes.values():
            status = "✅" if node.completed else "❌"
            parts.append(f"\n## {status} {node.role.name}: {node.node_id}")
            if node.result:
                parts.append(f"\n{node.result}")
            if node.error:
                parts.append(f"\n**Error:** {node.error}")

        return "\n".join(parts)

    async def process_wiki_updates(self) -> None:
        """Background task: process wiki update requests sequentially."""
        while True:
            try:
                _ = await asyncio.wait_for(
                    self._wiki_update_queue.get(),
                    timeout=1.0,
                )
                # Apply update to wiki (single authoritative owner)
                # In production, this would call wiki.create_or_update_page()
                pass
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_concurrency": self.max_concurrency,
            "max_agents": self.max_agents,
            "active_agents": len(self._agents),
            "agents": {aid: a.to_dict() for aid, a in self._agents.items()},
        }
