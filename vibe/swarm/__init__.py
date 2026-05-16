"""Multi-Agent Swarm Orchestration — specialized sub-agents collaborating via message bus.

SwarmOrchestrator spawns role-based sub-agents (Research, Coding, Critic)
that communicate through a Pub/Sub message bus with shared wiki access.
"""

from vibe.swarm.protocol import AgentMessage, MessageBus, MessageType, EventBroker
from vibe.swarm.agent import SubAgent, SubAgentRole, AgentLifecycle
from vibe.swarm.orchestrator import SwarmOrchestrator, SwarmResult, TaskDAG
from vibe.swarm.shared_wiki import SharedWiki, WikiUpdateRequest

__all__ = [
    "AgentMessage",
    "MessageBus",
    "MessageType",
    "EventBroker",
    "SubAgent",
    "SubAgentRole",
    "AgentLifecycle",
    "SwarmOrchestrator",
    "SwarmResult",
    "TaskDAG",
    "SharedWiki",
    "WikiUpdateRequest",
]
