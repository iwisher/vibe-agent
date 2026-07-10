"""Tests for SubAgent lifecycle, roles, and scratchpad."""

import asyncio

import pytest

from vibe.swarm.agent import (
    ROLE_PROMPTS,
    AgentLifecycle,
    Scratchpad,
    SubAgent,
    SubAgentConfig,
    SubAgentRole,
)
from vibe.swarm.protocol import MessageBus, MessageType


class TestScratchpad:
    def test_add_note(self):
        sp = Scratchpad()
        sp.add_note("Note 1")
        sp.add_note("Note 2")
        assert sp.notes == ["Note 1", "Note 2"]

    def test_add_artifact(self):
        sp = Scratchpad()
        sp.add_artifact("code", "print('hello')")
        assert sp.artifacts["code"] == "print('hello')"

    def test_to_dict(self):
        sp = Scratchpad()
        sp.add_note("Test")
        sp.add_artifact("result", 42)
        d = sp.to_dict()
        assert d["notes"] == ["Test"]
        assert d["artifacts"]["result"] == 42


class TestSubAgentConfig:
    def test_default_config(self):
        config = SubAgentConfig(role=SubAgentRole.RESEARCH)
        assert config.role == SubAgentRole.RESEARCH
        assert config.model == "gpt-4o-mini"
        assert config.max_iterations == 5
        assert config.timeout_seconds == 120.0

    def test_custom_config(self):
        config = SubAgentConfig(
            role=SubAgentRole.CODING,
            model="claude-3-sonnet",
            max_iterations=10,
            tools=["bash", "file"],
        )
        assert config.role == SubAgentRole.CODING
        assert config.model == "claude-3-sonnet"
        assert config.tools == ["bash", "file"]


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_lifecycle(self):
        bus = MessageBus()
        config = SubAgentConfig(role=SubAgentRole.RESEARCH)
        agent = SubAgent(agent_id="test-1", config=config, bus=bus)

        assert agent.lifecycle == AgentLifecycle.SPAWNED
        await agent.start()
        assert agent.lifecycle == AgentLifecycle.ACTIVE
        await agent.stop()
        assert agent.lifecycle == AgentLifecycle.TERMINATED

    @pytest.mark.asyncio
    async def test_system_prompt(self):
        bus = MessageBus()
        config = SubAgentConfig(role=SubAgentRole.CODING)
        agent = SubAgent(agent_id="test-1", config=config, bus=bus)
        assert "Coding Agent" in agent.system_prompt

    @pytest.mark.asyncio
    async def test_all_role_prompts(self):
        for role in SubAgentRole:
            assert role in ROLE_PROMPTS
            assert len(ROLE_PROMPTS[role]) > 0

    @pytest.mark.asyncio
    async def test_execute_task_and_report_result(self):
        bus = MessageBus()
        config = SubAgentConfig(role=SubAgentRole.RESEARCH)
        agent = SubAgent(agent_id="research-1", config=config, bus=bus)

        # Register orchestrator queue BEFORE starting agent
        orch_queue = await bus.register_agent("orchestrator")

        await agent.start()
        await asyncio.sleep(0.1)  # Let agent start its loop

        # Send task
        await bus.send(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            recipient="research-1",
            content="Research Python async patterns",
            correlation_id="task-1",
        )

        # Wait for result
        msg = await asyncio.wait_for(orch_queue.get(), timeout=3.0)

        assert msg.msg_type == MessageType.RESULT
        assert msg.sender == "research-1"
        assert "RESEARCH" in msg.content
        assert msg.correlation_id == "task-1"

        await agent.stop()

    @pytest.mark.asyncio
    async def test_scratchpad_accumulates_notes(self):
        bus = MessageBus()
        config = SubAgentConfig(role=SubAgentRole.CRITIC)
        agent = SubAgent(agent_id="critic-1", config=config, bus=bus)

        await agent.start()
        await asyncio.sleep(0.1)  # Let agent start its loop

        # Send critique
        await bus.send(
            msg_type=MessageType.CRITIQUE,
            sender="orchestrator",
            recipient="critic-1",
            content="Code has security issues",
            correlation_id="task-1",
        )

        # Give time to process
        await asyncio.sleep(0.3)

        assert len(agent.scratchpad.notes) > 0
        assert "Critique" in agent.scratchpad.notes[0]

        await agent.stop()

    @pytest.mark.asyncio
    async def test_to_dict(self):
        bus = MessageBus()
        config = SubAgentConfig(role=SubAgentRole.PLANNER)
        agent = SubAgent(agent_id="planner-1", config=config, bus=bus)

        d = agent.to_dict()
        assert d["agent_id"] == "planner-1"
        assert d["role"] == "PLANNER"
        assert d["lifecycle"] == "SPAWNED"
        assert "scratchpad" in d

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        bus = MessageBus()
        config = SubAgentConfig(role=SubAgentRole.RESEARCH)
        agent = SubAgent(agent_id="test-1", config=config, bus=bus)

        await agent.start()
        assert agent._task is not None
        await agent.stop()
        assert agent._task.cancelled() or agent._task.done()
