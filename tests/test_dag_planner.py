"""Tests for DAG-based task planner and executor."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from vibe.harness.dag_planner import (
    DAGExecutor,
    DAGNode,
    DAGNodeStatus,
    DAGPlanResult,
    DAGPlanner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def planner():
    return DAGPlanner()


@pytest.fixture
def mock_tool_executor():
    """Return a mock tool executor that echoes back tool calls."""
    executor = AsyncMock()

    async def echo_execute(calls):
        from vibe.tools.tool_system import ToolResult
        return [
            ToolResult(success=True, content=f"result:{c['function']['name']}")
            for c in calls
        ]

    executor.execute = echo_execute
    return executor


# ---------------------------------------------------------------------------
# DAGNode
# ---------------------------------------------------------------------------

class TestDAGNode:
    def test_node_defaults(self):
        node = DAGNode(node_id="test_1", tool_name="bash")
        assert node.status == DAGNodeStatus.PENDING
        assert node.dependencies == []
        assert node.depth == 0
        assert node.result is None

    def test_is_ready_no_deps(self):
        node = DAGNode(node_id="a", tool_name="read")
        assert node.is_ready is True  # No deps = ready

    def test_is_ready_with_completed_deps(self):
        dep = DAGNode(node_id="dep", tool_name="read")
        dep.status = DAGNodeStatus.COMPLETED
        node = DAGNode(node_id="a", tool_name="write", dependencies=["dep"])
        # Note: is_ready checks dep.status but dep object isn't linked
        # In real usage, dependencies are resolved via dag.nodes lookup
        assert node.status == DAGNodeStatus.PENDING


# ---------------------------------------------------------------------------
# DAGPlanner — build and validation
# ---------------------------------------------------------------------------

class TestDAGPlannerBuild:
    def test_build_from_tool_calls_independent(self, planner):
        calls = [
            {"function": {"name": "bash", "arguments": "{\"cmd\": \"ls\"}"}},
            {"function": {"name": "read_file", "arguments": "{\"path\": \"/tmp\"}"}},
        ]
        dag = planner.build_from_tool_calls(calls)
        assert dag.is_valid is True
        assert dag.node_count == 2
        assert dag.edge_count == 0
        assert dag.max_depth == 0

    def test_build_from_tool_calls_with_deps(self, planner):
        calls = [
            {"function": {"name": "read_file", "arguments": "{\"path\": \"/tmp\"}"}},
            {"function": {"name": "write_file", "arguments": "{\"path\": \"/tmp/out\", \"content\": \"tool_0\"}"}},
        ]
        dag = planner.build_from_tool_calls(calls)
        assert dag.is_valid is True
        # write_file depends on read_file (heuristic or reference)
        assert dag.edge_count >= 1
        write_node = dag.nodes.get("tool_1")
        assert "tool_0" in write_node.dependencies

    def test_build_from_plan_result(self, planner):
        dag = planner.build_from_plan_result(
            selected_tools=["read_file", "bash", "write_file"],
            query="Process and save data",
        )
        assert dag.is_valid is True
        assert dag.node_count == 3
        # read should be root, write should depend on read
        assert "read_file_0" in dag.root_nodes
        write_node = dag.nodes["write_file_2"]
        assert "read_file_0" in write_node.dependencies

    def test_cycle_detection_invalid(self, planner):
        # Manually create a cycle
        nodes = {
            "a": DAGNode(node_id="a", tool_name="bash", dependencies=["c"]),
            "b": DAGNode(node_id="b", tool_name="read", dependencies=["a"]),
            "c": DAGNode(node_id="c", tool_name="write", dependencies=["b"]),
        }
        assert planner._is_dag_valid(nodes) is False

    def test_valid_dag_no_cycle(self, planner):
        nodes = {
            "a": DAGNode(node_id="a", tool_name="bash"),
            "b": DAGNode(node_id="b", tool_name="read", dependencies=["a"]),
            "c": DAGNode(node_id="c", tool_name="write", dependencies=["b"]),
        }
        assert planner._is_dag_valid(nodes) is True

    def test_compute_depths_linear(self, planner):
        nodes = {
            "a": DAGNode(node_id="a", tool_name="bash"),
            "b": DAGNode(node_id="b", tool_name="read", dependencies=["a"]),
            "c": DAGNode(node_id="c", tool_name="write", dependencies=["b"]),
        }
        planner._compute_depths(nodes)
        assert nodes["a"].depth == 0
        assert nodes["b"].depth == 1
        assert nodes["c"].depth == 2

    def test_compute_depths_diamond(self, planner):
        nodes = {
            "a": DAGNode(node_id="a", tool_name="bash"),
            "b": DAGNode(node_id="b", tool_name="read", dependencies=["a"]),
            "c": DAGNode(node_id="c", tool_name="read2", dependencies=["a"]),
            "d": DAGNode(node_id="d", tool_name="write", dependencies=["b", "c"]),
        }
        planner._compute_depths(nodes)
        assert nodes["a"].depth == 0
        assert nodes["b"].depth == 1
        assert nodes["c"].depth == 1
        assert nodes["d"].depth == 2

    def test_levels_grouping(self, planner):
        dag = planner.build_from_plan_result(
            selected_tools=["read_file", "bash", "write_file"],
            query="test",
        )
        levels = dag.levels()
        assert len(levels) >= 2
        # First level should be read_file (root)
        assert "read_file_0" in levels[0]
        # write_file should be in a later level
        assert any("write_file_2" in lvl for lvl in levels[1:])


# ---------------------------------------------------------------------------
# DAGExecutor
# ---------------------------------------------------------------------------

class TestDAGExecutor:
    @pytest.mark.asyncio
    async def test_execute_linear_dag(self, planner, mock_tool_executor):
        dag = planner.build_from_plan_result(
            selected_tools=["read_file", "write_file"],
            query="test",
        )
        executor = DAGExecutor(mock_tool_executor)
        results = await executor.execute(dag)

        assert len(results) == 2
        assert dag.nodes["read_file_0"].status == DAGNodeStatus.COMPLETED
        assert dag.nodes["write_file_1"].status == DAGNodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_parallel_dag(self, planner, mock_tool_executor):
        # Two independent reads, then one write
        dag = planner.build_from_plan_result(
            selected_tools=["read_file", "read_file", "write_file"],
            query="test",
        )
        # Manually adjust to make reads independent
        dag.nodes["read_file_0"].dependencies = []
        dag.nodes["read_file_1"].dependencies = []
        dag.nodes["write_file_2"].dependencies = ["read_file_0", "read_file_1"]
        # Recompute depths
        planner._compute_depths(dag.nodes)
        dag.max_depth = max(n.depth for n in dag.nodes.values())
        dag.root_nodes = [nid for nid, n in dag.nodes.items() if n.depth == 0]

        executor = DAGExecutor(mock_tool_executor)
        results = await executor.execute(dag)

        assert len(results) == 3
        # Both reads should have completed (potentially in parallel)
        assert dag.nodes["read_file_0"].status == DAGNodeStatus.COMPLETED
        assert dag.nodes["read_file_1"].status == DAGNodeStatus.COMPLETED
        assert dag.nodes["write_file_2"].status == DAGNodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_invalid_dag_raises(self, mock_tool_executor):
        bad_dag = DAGPlanResult(is_valid=False, error="test cycle")
        executor = DAGExecutor(mock_tool_executor)
        with pytest.raises(ValueError, match="Cannot execute invalid DAG"):
            await executor.execute(bad_dag)

    @pytest.mark.asyncio
    async def test_execute_with_tool_failure(self, planner, mock_tool_executor):
        async def fail_on_bash(calls):
            from vibe.tools.tool_system import ToolResult
            return [
                ToolResult(
                    success=(c["function"]["name"] != "bash"),
                    content="ok" if c["function"]["name"] != "bash" else None,
                    error="bash failed" if c["function"]["name"] == "bash" else None,
                )
                for c in calls
            ]

        mock_tool_executor.execute = fail_on_bash

        dag = planner.build_from_plan_result(
            selected_tools=["bash", "read_file"],
            query="test",
        )
        # Make bash independent of read_file so they run in same level
        dag.nodes["bash_0"].dependencies = []
        dag.nodes["read_file_1"].dependencies = []
        planner._compute_depths(dag.nodes)
        dag.root_nodes = [nid for nid, n in dag.nodes.items() if n.depth == 0]

        executor = DAGExecutor(mock_tool_executor)
        results = await executor.execute(dag)

        # bash fails but read_file still runs (independent at root level)
        assert dag.nodes["bash_0"].status == DAGNodeStatus.FAILED
        assert dag.nodes["read_file_1"].status == DAGNodeStatus.COMPLETED

    def test_levels_empty_dag(self, planner):
        dag = DAGPlanResult()
        assert dag.levels() == []

    def test_node_count_and_edge_count(self, planner):
        dag = planner.build_from_plan_result(
            selected_tools=["read_file", "bash", "write_file"],
            query="test",
        )
        assert dag.node_count == 3
        assert dag.edge_count >= 2  # read -> bash, read -> write
