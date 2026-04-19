"""Query loop implementation for Vibe Agent."""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import AsyncIterator, Callable, Optional, Any

from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.core.context_compactor import ContextCompactor
from vibe.core.error_recovery import ErrorRecovery, RetryPolicy
from vibe.harness.constraints import HookPipeline, HookOutcome
from vibe.harness.feedback import FeedbackEngine
from vibe.harness.instructions import InstructionSet
from vibe.harness.planner import ContextPlanner, PlanRequest, PlanResult
from vibe.tools.mcp_bridge import MCPBridge
from vibe.tools.tool_system import ToolSystem, ToolResult
from vibe.tools._utils import extract_tool_call_name, extract_tool_call_arguments


class QueryState(Enum):
    IDLE = auto()
    PLANNING = auto()
    PROCESSING = auto()
    TOOL_EXECUTION = auto()
    SYNTHESIZING = auto()
    COMPLETED = auto()
    INCOMPLETE = auto()
    STOPPED = auto()
    ERROR = auto()


@dataclass
class Metrics:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    tokens_per_second: float = 0.0


@dataclass
class Message:
    role: str
    content: str
    tool_calls: Optional[list] = None
    tool_call_id: Optional[str] = None
    model_version: Optional[str] = None


@dataclass
class QueryResult:
    response: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Optional[Exception] = None
    context_truncated: bool = False
    metrics: Optional[Metrics] = None
    state: QueryState = QueryState.IDLE


class QueryLoop:
    """Main query loop that manages conversation flow with harness hooks and state machine."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_system: ToolSystem,
        context_compactor: Optional[ContextCompactor] = None,
        error_recovery: Optional[ErrorRecovery] = None,
        hook_pipeline: Optional[HookPipeline] = None,
        feedback_engine: Optional[FeedbackEngine] = None,
        feedback_threshold: float = 0.7,
        max_feedback_retries: int = 1,
        max_iterations: int = 50,
        max_context_tokens: int = 8000,
        instruction_set: Optional[InstructionSet] = None,
        mcp_bridge: Optional[MCPBridge] = None,
        context_planner: Optional[ContextPlanner] = None,
        trace_store: Optional[Any] = None,
        config: Optional[Any] = None,
    ):
        # Allow VibeConfig to override individual parameters
        if config is not None:
            ql_cfg = getattr(config, "query_loop", None)
            if ql_cfg is not None:
                feedback_threshold = getattr(ql_cfg, "feedback_threshold", feedback_threshold)
                max_feedback_retries = getattr(ql_cfg, "max_feedback_retries", max_feedback_retries)
                max_iterations = getattr(ql_cfg, "max_iterations", max_iterations)
                max_context_tokens = getattr(ql_cfg, "max_context_tokens", max_context_tokens)
            retry_cfg = getattr(config, "retry", None)
            if retry_cfg is not None and error_recovery is None:
                error_recovery = ErrorRecovery(
                    RetryPolicy(
                        max_retries=getattr(retry_cfg, "max_retries", 2),
                        initial_delay=getattr(retry_cfg, "initial_delay", 1.0),
                    )
                )
            if context_compactor is None:
                context_compactor = ContextCompactor(max_tokens=max_context_tokens, config=config)

        self.llm = llm_client
        self.tools = tool_system
        self.compactor = context_compactor or ContextCompactor(max_tokens=max_context_tokens)
        self.error_recovery = error_recovery or ErrorRecovery(RetryPolicy())
        self.hook_pipeline = hook_pipeline or HookPipeline()
        self.feedback_engine = feedback_engine
        self.feedback_threshold = feedback_threshold
        self.max_feedback_retries = max_feedback_retries
        self.max_iterations = max_iterations
        self.messages: list[Message] = []
        self._running = False
        self._state = QueryState.IDLE
        self._tool_handlers: dict[str, Callable] = {}
        self._feedback_retries = 0
        self.instruction_set = instruction_set
        self.mcp_bridge = mcp_bridge
        self.context_planner = context_planner or ContextPlanner(trace_store=trace_store)
        self._plan_result: Optional[PlanResult] = None

    @property
    def state(self) -> QueryState:
        return self._state

    def _set_state(self, state: QueryState) -> None:
        self._state = state

    def register_tool_handler(self, tool_name: str, handler: Callable) -> None:
        self._tool_handlers[tool_name] = handler

    def set_model(self, model: str) -> str:
        old_model = self.llm.model
        self.llm.model = model
        self.messages.append(
            Message(role="system", content=f"Model switched to '{model}'", model_version=model)
        )
        return old_model

    def get_model(self) -> str:
        return self.llm.model

    async def run(self, initial_query: Optional[str] = None) -> AsyncIterator[QueryResult]:
        if self._state == QueryState.STOPPED:
            return
        self._running = True
        self._set_state(QueryState.PLANNING)
        if initial_query:
            self.messages.append(Message(role="user", content=initial_query))

        # --- Planning: tool, skill, and MCP selection ---
        self._plan_result = None
        if initial_query:
            plan_request = PlanRequest(
                query=initial_query,
                available_tools=self.tools.get_tool_schemas() + (self.mcp_bridge.get_tool_schemas() if self.mcp_bridge else []),
                available_skills=self.instruction_set.skills if self.instruction_set else [],
                available_mcps=[
                    {"name": cfg.name, "description": cfg.description}
                    for cfg in (self.mcp_bridge.configs if self.mcp_bridge else [])
                ],
            )
            self._plan_result = self.context_planner.plan(plan_request)
            if self._plan_result.system_prompt_append:
                self.messages.insert(
                    0,
                    Message(role="system", content=self._plan_result.system_prompt_append),
                )

        iteration = 0
        while self._running and iteration < self.max_iterations:
            iteration += 1
            self._set_state(QueryState.PROCESSING)
            try:
                llm_msgs = self._build_llm_messages()
                compacted = await self._maybe_compact(llm_msgs)
                if compacted:
                    yield compacted
                    llm_msgs = self._build_llm_messages()

                tools_for_llm = self._select_tools_for_llm()
                start_time = time.time()
                response = await self.error_recovery.execute_with_retry(
                    lambda: self.llm.complete(llm_msgs, tools=tools_for_llm)
                )
                elapsed = time.time() - start_time
                metrics = self._calc_metrics(response, elapsed)

                if response.is_error:
                    self._set_state(QueryState.ERROR)
                    yield QueryResult(
                        response="", error=Exception(response.error), metrics=metrics, state=self._state
                    )
                    break

                if not response.content and not response.tool_calls:
                    self._set_state(QueryState.ERROR)
                    yield QueryResult(
                        response="", error=Exception("Empty response"), metrics=metrics, state=self._state
                    )
                    break

                if response.tool_calls:
                    yield await self._process_tool_response(response, metrics)
                else:
                    should_continue, result = await self._process_content_response(response, metrics)
                    if result:
                        yield result
                    if not should_continue:
                        break

            except Exception as e:
                self._set_state(QueryState.ERROR)
                yield QueryResult(response="", error=e, state=self._state)
                break

        if self._state not in (QueryState.COMPLETED, QueryState.ERROR, QueryState.STOPPED):
            # Distinguish between natural completion and max_iterations exhaustion
            if iteration >= self.max_iterations:
                self._set_state(QueryState.INCOMPLETE)
            else:
                self._set_state(QueryState.COMPLETED)

    async def _maybe_compact(self, llm_msgs: list[dict]) -> Optional[QueryResult]:
        """Compact context if needed. Returns a QueryResult if compaction occurred."""
        if not self.compactor.should_compact(llm_msgs):
            return None
        compacted = await self.compactor.compact_async(llm_msgs)
        self.messages = [
            Message(
                role=m["role"],
                content=m.get("content", ""),
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id"),
            )
            for m in compacted.messages
        ]
        return QueryResult(
            response="",
            context_truncated=compacted.was_compacted,
            state=QueryState.PROCESSING,
        )

    def _select_tools_for_llm(self) -> list[dict]:
        """Select tools based on planner result, with safety fallback."""
        internal_schemas = self.tools.get_tool_schemas()
        mcp_schemas = self.mcp_bridge.get_tool_schemas() if self.mcp_bridge else []
        all_schemas = internal_schemas + mcp_schemas
        tools_for_llm = all_schemas
        if self._plan_result and self._plan_result.selected_tool_names:
            tools_for_llm = [
                t for t in all_schemas if t.get("function", {}).get("name") in self._plan_result.selected_tool_names
            ]
        # Safety fallback: if planner filtered out everything, expose all tools
        if not tools_for_llm:
            tools_for_llm = all_schemas
        return tools_for_llm

    async def _process_tool_response(self, response: LLMResponse, metrics: Metrics) -> QueryResult:
        """Handle a response containing tool calls."""
        self._set_state(QueryState.TOOL_EXECUTION)
        tool_results = await self._execute_tool_calls(response.tool_calls)
        self.messages.append(
            Message(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
                model_version=self.llm.model,
            )
        )
        for call, result in zip(response.tool_calls, tool_results):
            if isinstance(call, dict):
                tool_call_id = call.get("id")
            else:
                tool_call_id = getattr(call, "id", None)
            self.messages.append(
                Message(
                    role="tool",
                    content=result.content if result.success else result.error,
                    tool_call_id=tool_call_id,
                )
            )
        self._set_state(QueryState.SYNTHESIZING)
        return QueryResult(
            response=response.content or "",
            tool_results=tool_results,
            metrics=metrics,
            state=self._state,
        )

    async def _process_content_response(self, response: LLMResponse, metrics: Metrics) -> tuple[bool, Optional[QueryResult]]:
        """Handle a response with no tool calls. Returns (should_continue, result_to_yield)."""
        self.messages.append(
            Message(role="assistant", content=response.content or "", model_version=self.llm.model)
        )
        # Feedback loop: evaluate response before completing
        should_continue = await self._apply_feedback(response, metrics)
        if should_continue:
            return True, QueryResult(
                response=response.content or "",
                metrics=metrics,
                state=QueryState.PROCESSING,
            )
        self._set_state(QueryState.COMPLETED)
        return False, QueryResult(response=response.content or "", metrics=metrics, state=self._state)

    async def _apply_feedback(self, response: LLMResponse, metrics: Metrics) -> bool:
        """Apply feedback engine evaluation. Returns True if loop should continue."""
        if (
            self.feedback_engine
            and response.content
            and self._feedback_retries < self.max_feedback_retries
        ):
            fb = await self.feedback_engine.self_verify(response.content)
            if fb.score < self.feedback_threshold:
                self._feedback_retries += 1
                fix_hint = fb.suggested_fix or "Please improve your response."
                issues_text = "\n".join(f"- {i}" for i in fb.issues) if fb.issues else ""
                self.messages.append(
                    Message(
                        role="system",
                        content=(
                            f"Feedback score {fb.score:.2f} below threshold "
                            f"({self.feedback_threshold}). Issues:\n{issues_text}\n"
                            f"Suggested fix: {fix_hint}"
                        ),
                    )
                )
                self._set_state(QueryState.PROCESSING)
                return True
        return False

    async def _execute_tool_calls(self, tool_calls: list) -> list[ToolResult]:
        import json

        results = []
        for call in tool_calls:
            try:
                if isinstance(call, dict):
                    call_name = extract_tool_call_name(call)
                    arguments = extract_tool_call_arguments(call)
                else:
                    call_name = getattr(call, "name", None)
                    arguments = getattr(call, "arguments", {})

                # Run pre-hooks (Constraints: PRE_VALIDATE, PRE_MODIFY, PRE_ALLOW)
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

                if call_name in self._tool_handlers:
                    result = await self._tool_handlers[call_name](exec_args)
                else:
                    result = await self.tools.execute_tool(call_name, **exec_args)
                    if not result.success and "not found" in (result.error or "").lower() and self.mcp_bridge:
                        result = await self.mcp_bridge.execute_tool(call_name, **exec_args)

                # Run post-hooks (Constraints: POST_EXECUTE, POST_FIX)
                result = self.hook_pipeline.run_post_hooks(call_name, exec_args, result)
                results.append(result)
            except Exception as e:
                results.append(ToolResult(success=False, content=None, error=str(e)))
        return results

    def _build_llm_messages(self) -> list[dict]:
        return [
            {
                "role": msg.role,
                "content": msg.content,
                **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
                **({"tool_call_id": msg.tool_call_id} if msg.tool_call_id else {}),
            }
            for msg in self.messages
        ]

    def _calc_metrics(self, response: LLMResponse, elapsed: float) -> Metrics:
        usage = response.usage or {}
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        tt = usage.get("total_tokens", pt + ct)
        tps = ct / elapsed if elapsed > 0 else 0
        return Metrics(
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            elapsed_seconds=elapsed,
            tokens_per_second=tps,
        )

    def stop(self) -> None:
        self._running = False
        self._set_state(QueryState.STOPPED)

    def add_user_message(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))

    def clear_history(self) -> None:
        self.messages.clear()
        self._state = QueryState.IDLE
        self._feedback_retries = 0
        self._running = False
        self._plan_result = None

    async def close(self) -> None:
        """Closes the underlying LLM client and releases resources."""
        if self.llm is not None:
            await self.llm.close()
        if self.mcp_bridge is not None:
            await self.mcp_bridge.close()
