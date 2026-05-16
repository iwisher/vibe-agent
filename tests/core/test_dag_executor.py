"""Tests for DAG-based parallel tool execution."""

import pytest

from vibe.harness.dag_planner import DAGExecutor, DAGNode, DAGNodeStatus, DAGPlanner, DAGPlanResult


class MockToolExecutor:
    """Mock tool executor for testing DAG execution."""

    def __init__(self):
        self.call_log = []

    async def execute(self, tool_calls):
        results = []
        for call in tool_calls:
            self.call_log.append(call)
            tool_name = call["function"]["name"]
            # Return a mock result
            class MockResult:
                def __init__(self, name):
                    self.success = True
                    self.content = f"result_{name}"
                    self.tool_name = name

            results.append(MockResult(tool_name))
        return results


class TestDAGPlanner:
    def test_build_from_tool_calls_independent(self):
        planner = DAGPlanner()
        tool_calls = [
            {"function": {"name": "read_file", "arguments": '{"path": "/tmp/a"}'}},
            {"function": {"name": "read_file", "arguments": '{"path": "/tmp/b"}'}},
        ]
        dag = planner.build_from_tool_calls(tool_calls)
        assert dag.is_valid is True
        assert dag.node_count == 2
        assert dag.max_depth == 0  # No dependencies
        assert len(dag.root_nodes) == 2

    def test_build_from_tool_calls_with_deps(self):
        planner = DAGPlanner()
        tool_calls = [
            {"function": {"name": "read_file", "arguments": '{"path": "/tmp/a"}'}},
            {"function": {"name": "write_file", "arguments": '{"path": "/tmp/b", "content": "tool_0"}'}},
        ]
        dag = planner.build_from_tool_calls(tool_calls)
        assert dag.is_valid is True
        assert dag.node_count == 2
        # write_file depends on read_file (content references tool_0)
        write_node = dag.nodes["tool_1"]
        assert "tool_0" in write_node.dependencies

    def test_cycle_detection(self):
        planner = DAGPlanner()
        # Manually create cyclic dependencies
        dag = DAGPlanResult()
        dag.nodes = {
            "a": DAGNode("a", "tool_a", dependencies=["b"]),
            "b": DAGNode("b", "tool_b", dependencies=["a"]),
        }
        # Validate through planner
        valid = planner._is_dag_valid(dag.nodes)
        assert valid is False

    def test_levels_grouping(self):
        planner = DAGPlanner()
        tool_calls = [
            {"function": {"name": "read_file", "arguments": '{"path": "/tmp/a"}'}},
            {"function": {"name": "write_file", "arguments": '{"path": "/tmp/b", "content": "tool_0"}'}},
            {"function": {"name": "bash", "arguments": '{"command": "cat tool_1"}'}},
        ]
        dag = planner.build_from_tool_calls(tool_calls)
        levels = dag.levels()
        assert len(levels) == 3  # 3 levels due to chain of deps
        assert "tool_0" in levels[0]  # read_file (no deps)
        assert "tool_1" in levels[1]  # write_file (depends on read)
        assert "tool_2" in levels[2]  # bash (depends on write)

    def test_empty_tool_calls(self):
        planner = DAGPlanner()
        dag = planner.build_from_tool_calls([])
        assert dag.is_valid is True
        assert dag.node_count == 0


class TestDAGExecutor:
    @pytest.mark.asyncio
    async def test_execute_independent_tools(self):
        executor = MockToolExecutor()
        dag_executor = DAGExecutor(executor)

        dag = DAGPlanResult()
        dag.nodes = {
            "a": DAGNode("a", "tool_a"),
            "b": DAGNode("b", "tool_b"),
        }
        dag.root_nodes = ["a", "b"]
        dag.max_depth = 0
        dag.is_valid = True

        results = await dag_executor.execute(dag)
        assert len(results) == 2
        assert results["a"].content == "result_tool_a"
        assert results["b"].content == "result_tool_b"

    @pytest.mark.asyncio
    async def test_execute_with_dependencies(self):
        executor = MockToolExecutor()
        dag_executor = DAGExecutor(executor)

        dag = DAGPlanResult()
        dag.nodes = {
            "a": DAGNode("a", "tool_a"),
            "b": DAGNode("b", "tool_b", dependencies=["a"]),
        }
        dag.root_nodes = ["a"]
        dag.max_depth = 1
        dag.is_valid = True
        # Set depths manually
        dag.nodes["a"].depth = 0
        dag.nodes["b"].depth = 1

        results = await dag_executor.execute(dag)
        assert len(results) == 2
        assert dag.nodes["a"].status == DAGNodeStatus.COMPLETED
        assert dag.nodes["b"].status == DAGNodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_invalid_dag_raises(self):
        executor = MockToolExecutor()
        dag_executor = DAGExecutor(executor)

        dag = DAGPlanResult(is_valid=False, error="Cycle detected")
        with pytest.raises(ValueError, match="Cannot execute invalid DAG"):
            await dag_executor.execute(dag)

    def test_levels_property(self):
        dag = DAGPlanResult()
        dag.nodes = {
            "a": DAGNode("a", "tool_a", depth=0),
            "b": DAGNode("b", "tool_b", depth=0),
            "c": DAGNode("c", "tool_c", depth=1),
        }
        dag.is_valid = True
        levels = dag.levels()
        assert len(levels) == 2
        assert "a" in levels[0]
        assert "b" in levels[0]
        assert "c" in levels[1]
