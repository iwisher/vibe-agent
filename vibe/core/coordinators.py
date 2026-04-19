"""Coordinator submodules extracted from QueryLoop.

Each coordinator owns a distinct responsibility:
- ToolExecutor: tool call execution with hooks and MCP fallback
- FeedbackCoordinator: feedback engine integration and retry logic
- CompactionCoordinator: context compaction before LLM calls

This separation allows QueryLoop.run() to remain a thin orchestrator
(< 40 lines) and makes each component independently testable.
"""

from typing import Callable

from vibe.core.context_compactor import ContextCompactor
from vibe.core.model_gateway import LLMResponse
from vibe.harness.constraints import HookPipeline
from vibe.harness.feedback import FeedbackEngine
from vibe.tools.mcp_bridge import MCPBridge
from vibe.tools.tool_system import ToolSystem, ToolResult
from vibe.tools._utils import extract_tool_call_name, extract_tool_call_arguments


class ToolExecutor:
    """Executes tool calls with pre/post hooks and MCP fallback."""

    def __init__(
        self,
        tool_system: ToolSystem,
        hook_pipeline: HookPipeline,
        mcp_bridge: MCPBridge | None = None,
    ):
        self.tools = tool_system
        self.hook_pipeline = hook_pipeline
        self.mcp_bridge = mcp_bridge
        self._handlers: dict[str, Callable] = {}

    def register_handler(self, tool_name: str, handler: Callable) -> None:
        self._handlers[tool_name] = handler

    def select_tools(self, all_schemas: list[dict], selected_names: list[str] | None) -> list[dict]:
        """Filter schemas by planner selection, with safety fallback to all."""
        if not selected_names:
            return all_schemas
        filtered = [
            t for t in all_schemas
            if t.get("function", {}).get("name") in selected_names
        ]
        return filtered if filtered else all_schemas

    async def execute(self, tool_calls: list) -> list[ToolResult]:
        """Execute a batch of tool calls with hooks and fallback."""
        results = []
        for call in tool_calls:
            try:
                if isinstance(call, dict):
                    call_name = extract_tool_call_name(call)
                    arguments = extract_tool_call_arguments(call)
                else:
                    call_name = getattr(call, "name", None)
                    arguments = getattr(call, "arguments", {})

                # Pre-hooks
                pre_outcome = self.hook_pipeline.run_pre_hooks(call_name, arguments)
                if not pre_outcome.allow:
                    results.append(
                        ToolResult(
                            success=False,
                            content=None,
                            error=f"Hook veto: {pre_outcome.reason}",
                        )
                    )
                    continue

                exec_args = pre_outcome.modified_arguments or arguments

                if call_name in self._handlers:
                    result = await self._handlers[call_name](exec_args)
                else:
                    result = await self.tools.execute_tool(call_name, **exec_args)
                    if (
                        not result.success
                        and "not found" in (result.error or "").lower()
                        and self.mcp_bridge
                    ):
                        result = await self.mcp_bridge.execute_tool(call_name, **exec_args)

                # Post-hooks
                result = self.hook_pipeline.run_post_hooks(call_name, exec_args, result)
                results.append(result)
            except Exception as e:
                results.append(ToolResult(success=False, content=None, error=str(e)))
        return results


class FeedbackCoordinator:
    """Coordinates feedback engine evaluation and retry hints."""

    def __init__(
        self,
        feedback_engine: FeedbackEngine | None,
        threshold: float = 0.7,
        max_retries: int = 1,
    ):
        self.engine = feedback_engine
        self.threshold = threshold
        self.max_retries = max_retries
        self._retry_count = 0

    def reset(self) -> None:
        self._retry_count = 0

    async def evaluate(self, content: str) -> tuple[bool, str | None]:
        """Evaluate content and return (should_continue, fix_hint)."""
        if not self.engine or not content or self._retry_count >= self.max_retries:
            return False, None

        fb = await self.engine.self_verify(content)
        if fb.score >= self.threshold:
            return False, None

        self._retry_count += 1
        fix_hint = fb.suggested_fix or "Please improve your response."
        issues_text = "\n".join(f"- {i}" for i in fb.issues) if fb.issues else ""
        hint = (
            f"Feedback score {fb.score:.2f} below threshold "
            f"({self.threshold}). Issues:\n{issues_text}\n"
            f"Suggested fix: {fix_hint}"
        )
        return True, hint


class CompactionCoordinator:
    """Manages context compaction before LLM calls."""

    def __init__(self, compactor: ContextCompactor):
        self.compactor = compactor

    def should_compact(self, messages: list[dict]) -> bool:
        return self.compactor.should_compact(messages)

    async def compact(self, messages: list[dict]) -> tuple[list[dict], bool]:
        """Return (compacted_messages, was_compacted)."""
        if not self.should_compact(messages):
            return messages, False
        result = await self.compactor.compact_async(messages)
        return result.messages, result.was_compacted
