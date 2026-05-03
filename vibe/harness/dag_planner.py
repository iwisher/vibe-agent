"""DAG-based task planner for parallel tool execution.

Extends the existing planner with DAG output: tasks as nodes with
dependencies, enabling concurrent execution via asyncio.gather.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class DAGNodeStatus(Enum):
    """Execution status of a DAG node."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class DAGNode:
    """A single task node in the execution DAG.

    Attributes:
        node_id: Unique identifier (e.g., "bash_1", "file_read_2").
        tool_name: Name of the tool to execute.
        arguments: Tool arguments dict.
        dependencies: List of node_ids that must complete before this node runs.
        status: Current execution status.
        result: ToolResult after execution (None until completed).
        depth: Topological depth (0 = root, computed by Kahn's algorithm).
    """

    node_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    status: DAGNodeStatus = DAGNodeStatus.PENDING
    result: Any | None = None
    depth: int = 0

    @property
    def is_ready(self) -> bool:
        """True if all dependencies are completed."""
        return self.status == DAGNodeStatus.PENDING and all(
            dep.status == DAGNodeStatus.COMPLETED for dep in self.dependencies
        )


@dataclass
class DAGPlanResult:
    """Result of DAG planning: a set of nodes with dependency edges."""

    nodes: dict[str, DAGNode] = field(default_factory=dict)
    root_nodes: list[str] = field(default_factory=list)
    max_depth: int = 0
    is_valid: bool = True
    error: str | None = None

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(n.dependencies) for n in self.nodes.values())

    def levels(self) -> list[list[str]]:
        """Return nodes grouped by topological depth (parallel execution levels).

        Each inner list contains node_ids that can run concurrently.
        """
        if not self.is_valid:
            return []
        # Group by depth
        depth_map: dict[int, list[str]] = {}
        for node_id, node in self.nodes.items():
            depth_map.setdefault(node.depth, []).append(node_id)
        # Sort by depth
        return [depth_map[d] for d in sorted(depth_map.keys())]


# ---------------------------------------------------------------------------
# DAG builder and validator
# ---------------------------------------------------------------------------

class DAGPlanner:
    """Build and validate task DAGs from planner output."""

    def build_from_tool_calls(self, tool_calls: list[dict[str, Any]]) -> DAGPlanResult:
        """Build a DAG from a list of tool calls.

        Simple case: all tools are independent (no edges).
        Advanced case: infer dependencies from argument references.
        """
        nodes: dict[str, DAGNode] = {}
        for i, call in enumerate(tool_calls):
            node_id = f"tool_{i}"
            tool_name = self._extract_tool_name(call)
            arguments = self._extract_arguments(call)
            nodes[node_id] = DAGNode(
                node_id=node_id,
                tool_name=tool_name,
                arguments=arguments,
            )

        # Infer dependencies: if a node's argument references another node's output
        self._infer_dependencies(nodes)

        # Validate and compute depths
        if not self._is_dag_valid(nodes):
            return DAGPlanResult(is_valid=False, error="Cycle detected in task DAG")

        self._compute_depths(nodes)

        root_nodes = [nid for nid, n in nodes.items() if n.depth == 0]
        max_depth = max((n.depth for n in nodes.values()), default=0)

        return DAGPlanResult(
            nodes=nodes,
            root_nodes=root_nodes,
            max_depth=max_depth,
            is_valid=True,
        )

    def build_from_plan_result(
        self,
        selected_tools: list[str],
        query: str,
        tool_schemas: list[dict] | None = None,
    ) -> DAGPlanResult:
        """Build a DAG from a planner's selected tool list.

        Heuristic: file reads happen before writes; bash commands after reads.
        """
        nodes: dict[str, DAGNode] = {}
        for i, tool_name in enumerate(selected_tools):
            node_id = f"{tool_name}_{i}"
            nodes[node_id] = DAGNode(
                node_id=node_id,
                tool_name=tool_name,
                arguments={"query": query},
            )

        # Heuristic dependencies
        self._apply_heuristic_deps(nodes)

        if not self._is_dag_valid(nodes):
            return DAGPlanResult(is_valid=False, error="Cycle detected in heuristic DAG")

        self._compute_depths(nodes)

        root_nodes = [nid for nid, n in nodes.items() if n.depth == 0]
        max_depth = max((n.depth for n in nodes.values()), default=0)

        return DAGPlanResult(
            nodes=nodes,
            root_nodes=root_nodes,
            max_depth=max_depth,
            is_valid=True,
        )

    def _extract_tool_name(self, call: dict[str, Any]) -> str:
        """Extract tool name from a tool call dict."""
        if "function" in call:
            return call["function"].get("name", "unknown")
        return call.get("name", "unknown")

    def _extract_arguments(self, call: dict[str, Any]) -> dict[str, Any]:
        """Extract arguments from a tool call dict."""
        if "function" in call:
            args = call["function"].get("arguments", "{}")
            if isinstance(args, str):
                import json
                try:
                    return json.loads(args)
                except json.JSONDecodeError:
                    return {"raw": args}
            return args
        return call.get("arguments", {})

    def _infer_dependencies(self, nodes: dict[str, DAGNode]) -> None:
        """Infer dependencies from argument cross-references.

        Uses word-boundary regex matching to avoid false positives
        (e.g., 'tool_1' matching inside 'tool_10').
        """
        import re
        for node_id, node in nodes.items():
            arg_str = str(node.arguments)
            for other_id, other in nodes.items():
                if other_id != node_id:
                    # Word-boundary match: other_id must appear as a standalone token
                    pattern = r'\b' + re.escape(other_id) + r'\b'
                    if re.search(pattern, arg_str):
                        if other_id not in node.dependencies:
                            node.dependencies.append(other_id)

    def _apply_heuristic_deps(self, nodes: dict[str, DAGNode]) -> None:
        """Apply heuristic dependency rules.

        - read_file before write_file
        - read_file before bash (if bash references a file)
        - bash before write_file (if write references bash output)
        """
        read_nodes = [nid for nid, n in nodes.items() if "read" in n.tool_name.lower()]
        write_nodes = [nid for nid, n in nodes.items() if "write" in n.tool_name.lower()]
        bash_nodes = [nid for nid, n in nodes.items() if "bash" in n.tool_name.lower()]

        # Reads before writes
        for write_id in write_nodes:
            for read_id in read_nodes:
                if read_id not in nodes[write_id].dependencies:
                    nodes[write_id].dependencies.append(read_id)

        # Reads before bash (conservative: bash might need file context)
        for bash_id in bash_nodes:
            for read_id in read_nodes:
                if read_id not in nodes[bash_id].dependencies:
                    nodes[bash_id].dependencies.append(read_id)

    def _is_dag_valid(self, nodes: dict[str, DAGNode]) -> bool:
        """Check for cycles using Kahn's algorithm.

        Returns True if the graph is a valid DAG (no cycles).
        """
        # Build in-degree map
        in_degree: dict[str, int] = {nid: 0 for nid in nodes}
        for node in nodes.values():
            for dep in node.dependencies:
                if dep in in_degree:
                    in_degree[node.node_id] += 1

        # Start with nodes that have no dependencies
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0

        while queue:
            current = queue.pop(0)
            visited += 1
            # Find nodes that depend on current
            for node in nodes.values():
                if current in node.dependencies:
                    in_degree[node.node_id] -= 1
                    if in_degree[node.node_id] == 0:
                        queue.append(node.node_id)

        return visited == len(nodes)

    def _compute_depths(self, nodes: dict[str, DAGNode]) -> None:
        """Compute topological depth for each node using BFS.

        Depth 0 = no dependencies. Depth N = longest dependency chain length.
        """
        # Reset depths
        for node in nodes.values():
            node.depth = 0

        # BFS from roots
        queue = [nid for nid, n in nodes.items() if not n.dependencies]
        visited = set(queue)

        while queue:
            current = queue.pop(0)
            current_depth = nodes[current].depth
            # Find nodes that depend on current
            for node in nodes.values():
                if current in node.dependencies:
                    node.depth = max(node.depth, current_depth + 1)
                    if node.node_id not in visited:
                        visited.add(node.node_id)
                        queue.append(node.node_id)


# ---------------------------------------------------------------------------
# DAG execution engine (for ToolExecutor)
# ---------------------------------------------------------------------------

class DAGExecutor:
    """Execute a DAGPlanResult using level-parallel asyncio.gather.

    Each level (nodes at the same depth) runs concurrently.
    Dependencies are resolved before each level starts.
    """

    def __init__(self, tool_executor: Any):
        self.tool_executor = tool_executor

    async def execute(self, dag: DAGPlanResult) -> dict[str, Any]:
        """Execute all nodes in the DAG level by level.

        Returns:
            Dict mapping node_id to result (or exception for failed nodes).
        """
        if not dag.is_valid:
            raise ValueError(f"Cannot execute invalid DAG: {dag.error}")

        levels = dag.levels()
        results: dict[str, Any] = {}

        for level in levels:
            # Run all nodes at this level concurrently
            tasks = []
            for node_id in level:
                node = dag.nodes[node_id]
                node.status = DAGNodeStatus.RUNNING
                # Inject dependency results into arguments
                args = self._resolve_arguments(node, results)
                tasks.append(self._execute_node(node, args))

            level_results = await __import__("asyncio").gather(*tasks, return_exceptions=True)

            for node_id, result in zip(level, level_results):
                node = dag.nodes[node_id]
                # Check for failure: either an Exception or a ToolResult with success=False
                is_failure = isinstance(result, Exception) or (
                    hasattr(result, "success") and not getattr(result, "success", True)
                )
                if is_failure:
                    node.status = DAGNodeStatus.FAILED
                    node.result = result
                    results[node_id] = result
                else:
                    node.status = DAGNodeStatus.COMPLETED
                    node.result = result
                    results[node_id] = result

        return results

    def _resolve_arguments(self, node: DAGNode, results: dict[str, Any]) -> dict[str, Any]:
        """Replace argument placeholders with dependency results.

        If a dependency failed, mark this node SKIPPED and return empty args.
        """
        # Check if any dependency failed
        for dep_id in node.dependencies:
            dep = results.get(dep_id)
            if isinstance(dep, Exception) or (hasattr(dep, "success") and not getattr(dep, "success", True)):
                node.status = DAGNodeStatus.SKIPPED
                return {}

        args = dict(node.arguments)
        for dep_id in node.dependencies:
            dep_result = results.get(dep_id)
            if dep_result is not None:
                # Simple substitution: if arg value equals dep_id, replace with result
                for key, val in list(args.items()):
                    if val == dep_id or (isinstance(val, str) and dep_id in val):
                        args[key] = dep_result
        return args

    async def _execute_node(self, node: DAGNode, args: dict[str, Any]) -> Any:
        """Execute a single DAG node via the tool executor."""
        # Build a synthetic tool call
        tool_call = {
            "id": node.node_id,
            "function": {
                "name": node.tool_name,
                "arguments": args,
            },
        }
        executed = await self.tool_executor.execute([tool_call])
        return executed[0] if executed else None
