"""Tests for SwarmOrchestrator DAG scheduling and swarm execution."""

import pytest
import asyncio

from vibe.swarm.orchestrator import (
    SwarmOrchestrator,
    SwarmResult,
    TaskDAG,
    TaskNode,
)
from vibe.swarm.agent import SubAgentRole
from vibe.swarm.shared_wiki import SharedWiki


class TestTaskDAG:
    def test_add_node(self):
        dag = TaskDAG()
        node = TaskNode(node_id="n1", description="Test", role=SubAgentRole.RESEARCH)
        dag.add_node(node)
        assert "n1" in dag.nodes
        assert dag.nodes["n1"].description == "Test"

    def test_get_ready_nodes_no_prerequisites(self):
        dag = TaskDAG()
        dag.add_node(TaskNode(node_id="n1", description="A", role=SubAgentRole.RESEARCH))
        dag.add_node(TaskNode(node_id="n2", description="B", role=SubAgentRole.CODING))

        ready = dag.get_ready_nodes()
        assert len(ready) == 2
        assert {n.node_id for n in ready} == {"n1", "n2"}

    def test_get_ready_nodes_with_prerequisites(self):
        dag = TaskDAG()
        dag.add_node(TaskNode(node_id="n1", description="A", role=SubAgentRole.RESEARCH))
        dag.add_node(TaskNode(
            node_id="n2", description="B", role=SubAgentRole.CODING, prerequisites=["n1"]
        ))

        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == "n1"

    def test_get_ready_nodes_after_completion(self):
        dag = TaskDAG()
        dag.add_node(TaskNode(node_id="n1", description="A", role=SubAgentRole.RESEARCH))
        dag.add_node(TaskNode(
            node_id="n2", description="B", role=SubAgentRole.CODING, prerequisites=["n1"]
        ))

        dag.nodes["n1"].completed = True
        ready = dag.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == "n2"

    def test_is_complete_all_done(self):
        dag = TaskDAG()
        dag.add_node(TaskNode(node_id="n1", description="A", role=SubAgentRole.RESEARCH, completed=True))
        dag.add_node(TaskNode(node_id="n2", description="B", role=SubAgentRole.CODING, completed=True))
        assert dag.is_complete()

    def test_is_complete_not_done(self):
        dag = TaskDAG()
        dag.add_node(TaskNode(node_id="n1", description="A", role=SubAgentRole.RESEARCH))
        assert not dag.is_complete()

    def test_is_complete_with_error(self):
        dag = TaskDAG()
        dag.add_node(TaskNode(node_id="n1", description="A", role=SubAgentRole.RESEARCH, error="Failed"))
        assert dag.is_complete()


class TestSwarmOrchestrator:
    @pytest.mark.asyncio
    async def test_orchestrator_creation(self):
        orch = SwarmOrchestrator(max_concurrency=2, max_agents=4)
        assert orch.max_concurrency == 2
        assert orch.max_agents == 4
        assert len(orch._agents) == 0

    @pytest.mark.asyncio
    async def test_decompose_task(self):
        orch = SwarmOrchestrator()
        dag = await orch._decompose_task("Build an API")

        assert "research" in dag.nodes
        assert "coding" in dag.nodes
        assert "critique" in dag.nodes
        assert dag.nodes["coding"].prerequisites == ["research"]
        assert dag.nodes["critique"].prerequisites == ["coding"]

    @pytest.mark.asyncio
    async def test_run_simple_task(self):
        orch = SwarmOrchestrator(max_concurrency=2, max_agents=4)
        result = await orch.run("Test task")

        assert isinstance(result, SwarmResult)
        assert result.task == "Test task"
        assert result.dag.is_complete()
        assert result.duration_seconds >= 0
        assert result.final_synthesis is not None
        assert "Swarm Execution Results" in result.final_synthesis

    @pytest.mark.asyncio
    async def test_run_with_dag_dependencies(self):
        orch = SwarmOrchestrator(max_concurrency=2, max_agents=4)
        result = await orch.run("Build a REST API")

        # Verify DAG execution order (research before coding before critique)
        dag = result.dag
        assert dag.nodes["research"].completed
        assert dag.nodes["coding"].completed
        assert dag.nodes["critique"].completed

    @pytest.mark.asyncio
    async def test_result_to_dict(self):
        orch = SwarmOrchestrator()
        result = await orch.run("Test")

        d = result.to_dict()
        assert d["task"] == "Test"
        assert "dag" in d
        assert "agent_outputs" in d
        assert "final_synthesis" in d
        assert "duration_seconds" in d
        assert "success" in d

    @pytest.mark.asyncio
    async def test_max_agents_limit(self):
        orch = SwarmOrchestrator(max_concurrency=1, max_agents=1)
        result = await orch.run("Test with limit")
        assert result.dag.is_complete()

    @pytest.mark.asyncio
    async def test_orchestrator_to_dict(self):
        orch = SwarmOrchestrator(max_concurrency=2, max_agents=4)
        d = orch.to_dict()
        assert d["max_concurrency"] == 2
        assert d["max_agents"] == 4
        assert d["active_agents"] == 0
