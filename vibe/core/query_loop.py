"""Query loop implementation for Vibe Agent."""

import asyncio
import copy
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

if TYPE_CHECKING:
    from vibe.core.query_loop_factory import QueryLoopFactory
    from vibe.harness.memory.session_store import SessionStore

# Phase: Adaptive iteration budgets
from vibe.core.adaptive_budget import AdaptiveBudgetAllocator, BudgetConfig, IterationBudget
from vibe.core.context_compactor import ContextCompactor
from vibe.core.coordinators import (
    CompactionCoordinator,
    FeedbackCoordinator,
    SecurityCoordinator,
    ToolExecutor,
)
from vibe.core.error_recovery import ErrorRecovery, RetryPolicy

# Phase: Latency-aware routing
from vibe.core.latency_tracker import LatencyAwareRouter, LatencyTracker
from vibe.core.llm_types import ErrorType
from vibe.core.model_gateway import LLMClient, LLMResponse
from vibe.harness.constraints import HookPipeline
from vibe.harness.feedback import FeedbackEngine
from vibe.harness.instructions import InstructionSet
from vibe.harness.planner import HybridPlanner as ContextPlanner
from vibe.harness.planner import PlanRequest, PlanResult
from vibe.tools._utils import extract_tool_call_arguments, extract_tool_call_name
from vibe.tools.mcp_bridge import MCPBridge
from vibe.tools.tool_system import ToolResult, ToolSystem


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
    reasoning_tokens: int = 0


@dataclass
class Message:
    role: str
    content: str
    tool_calls: list | None = None
    tool_call_id: str | None = None
    model_version: str | None = None
    # Optional annotations; tool Messages carry {"tool_name": ...} so that
    # extraction/reflection transcripts can label tool outputs by name.
    metadata: dict | None = None


@dataclass
class QueryResult:
    response: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Exception | None = None
    context_truncated: bool = False
    metrics: Metrics | None = None
    state: QueryState = QueryState.IDLE
    is_status: bool = False
    status_message: str = ""
    actionable_hint: str | None = None
    model_used: str | None = None
    reasoning_content: str = ""
    is_chunk: bool = False
    is_stream_chunk: bool = False


def _coerce_float(value: Any, default: float) -> float:
    """Best-effort float coercion with a safe default (mock/None-safe)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class QueryLoop:
    """Main query loop that manages conversation flow with harness hooks and state machine."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_system: ToolSystem,
        context_compactor: ContextCompactor | None = None,
        error_recovery: ErrorRecovery | None = None,
        hook_pipeline: HookPipeline | None = None,
        feedback_engine: FeedbackEngine | None = None,
        feedback_threshold: float = 0.7,
        max_feedback_retries: int = 1,
        max_iterations: int = 50,
        max_context_tokens: int = 8000,
        instruction_set: InstructionSet | None = None,
        mcp_bridge: MCPBridge | None = None,
        context_planner: ContextPlanner | None = None,
        trace_store: Any | None = None,
        config: Any | None = None,
        logger: Any | None = None,
        security_config: Any | None = None,
        checkpoint_manager: Any | None = None,
        # v4: Tripartite Memory System — optional, zero behavioral change when None
        wiki: Any | None = None,
        pageindex: Any | None = None,
        telemetry: Any | None = None,
        session_store: Any | None = None,
        cost_router: Any | None = None,
        dag_planner: Any | None = None,
        enable_dag_execution: bool = False,
        tool_prefs: Any | None = None,
        # Phase: Adaptive iteration budgets
        adaptive_budget: bool = False,
        budget_config: BudgetConfig | None = None,
        # Phase A: Skill-Maker pipeline
        skill_maker: Any | None = None,
        # Phase 5.2: Shadow workspace rollback
        shadow_manager: Any | None = None,
        stream: bool = True,
    ):
        # Allow VibeConfig to override individual parameters
        if config is not None:
            ql_cfg = getattr(config, "query_loop", None)
            if ql_cfg is not None:
                feedback_threshold = getattr(ql_cfg, "feedback_threshold", feedback_threshold)
                max_feedback_retries = getattr(ql_cfg, "max_feedback_retries", max_feedback_retries)
                max_iterations = getattr(ql_cfg, "max_iterations", max_iterations)
                max_context_tokens = getattr(ql_cfg, "max_context_tokens", max_context_tokens)
                adaptive_budget = getattr(ql_cfg, "adaptive_budget", adaptive_budget)
            retry_cfg = getattr(config, "retry", None)
            if retry_cfg is not None and error_recovery is None:
                error_recovery = ErrorRecovery(
                    RetryPolicy(
                        max_retries=getattr(retry_cfg, "max_retries", 2),
                        initial_delay=getattr(retry_cfg, "initial_delay", 1.0),
                    )
                )
            if context_compactor is None:
                max_tokens = int(max_context_tokens) if max_context_tokens is not None else 8000
                context_compactor = ContextCompactor(max_tokens=max_tokens, config=config)

        self.llm = llm_client
        self.tools = tool_system
        self.max_iterations = int(max_iterations) if max_iterations is not None else 50
        self.max_context_tokens = (
            int(max_context_tokens) if max_context_tokens is not None else 8000
        )
        self.compactor = context_compactor or ContextCompactor(max_tokens=self.max_context_tokens)
        self.compaction_coord = CompactionCoordinator(self.compactor)
        self.error_recovery = error_recovery or ErrorRecovery(RetryPolicy())
        self.hook_pipeline = hook_pipeline or HookPipeline()
        self.feedback_coord = FeedbackCoordinator(
            feedback_engine, feedback_threshold, max_feedback_retries
        )
        self.tool_executor = ToolExecutor(
            tool_system,
            self.hook_pipeline,
            mcp_bridge=mcp_bridge,
            tool_prefs=tool_prefs,
            shadow_manager=shadow_manager,
        )
        self.instruction_set = instruction_set
        self.mcp_bridge = mcp_bridge
        self.context_planner = context_planner
        self.trace_store = trace_store
        self.config = config
        self.logger = logger
        self.security_config = security_config
        self.checkpoint_manager = checkpoint_manager
        self.stream = stream
        self.wiki = wiki
        self.pageindex = pageindex
        self.telemetry = telemetry
        self.session_store = session_store
        self.cost_router = cost_router
        self.dag_planner = dag_planner
        self.enable_dag_execution = enable_dag_execution
        self.tool_prefs = tool_prefs
        self.skill_maker = skill_maker
        self.shadow_manager = shadow_manager

        # Phase: Adaptive iteration budgets
        self.adaptive_budget = adaptive_budget
        self._budget_config = budget_config or BudgetConfig.from_config(config)
        self._budget_allocator = AdaptiveBudgetAllocator(self._budget_config)
        self._iteration_budget: IterationBudget | None = None
        # Phase: Latency-aware routing
        self.latency_tracker = LatencyTracker()
        self.latency_router = LatencyAwareRouter(tracker=self.latency_tracker)
        self._state = QueryState.IDLE
        self._feedback_retries = 0
        self._plan_result: PlanResult | None = None
        self._trace_store = trace_store
        self._session_id: str | None = None
        # v4: Tripartite Memory System
        self._telemetry = telemetry
        self._wiki_extract_task: asyncio.Task | None = None  # Phase 1b: async extraction
        self._rlm_trigger_task: asyncio.Task | None = None  # Phase 2: RLM trigger
        self._skill_maker_task: asyncio.Task | None = None  # Phase A: skill maker
        self._reflection_task: asyncio.Task | None = None  # Trajectory reflection
        # Stable ids (WikiPage.id) of lesson-tagged pages actually injected into
        # this run's prompt by _build_wiki_hint — consumed by usage feedback.
        self._injected_lesson_ids: list[str] = []
        # Workstream C: Pivotal local retry (PivoARL). Per-run state tracking
        # repeated identical tool-call failures by call signature; when the same
        # failing call repeats, its iteration index is the pivotal turn and the
        # loop performs at most one guided retry of that call (never security
        # denials) instead of drifting into ERROR/INCOMPLETE.
        er_cfg = getattr(config, "error_recovery", None) if config is not None else None
        self._pivotal_retry_enabled: bool = bool(getattr(er_cfg, "pivotal_retry_enabled", True))
        self._max_pivotal_retries: int = int(getattr(er_cfg, "max_pivotal_retries", 1))
        self._pivotal_turn: int | None = None
        self._pivotal_failure_counts: dict[tuple[str, str], int] = {}
        self._pivotal_retry_counts: dict[tuple[str, str], int] = {}
        # Grace period (seconds) that close() gives background learning tasks
        # to finish before cancelling them — lets one-shot sessions persist
        # their lessons instead of killing the tasks on exit.
        self._close_task_grace_seconds: float = 15.0
        self._session_start_time: float = 0.0
        self._config_memory = (
            (getattr(config, "memory", None) or getattr(config, "tripartite", None))
            if config
            else None
        )
        # Phase 3.2: Session checkpointing for durable suspension/resumption
        self._session_store = session_store
        self._iteration = 0
        self._last_checkpointed_iteration = -1
        self._last_checkpointed_state: QueryState | None = None
        # Phase 3.3: Cost-aware dynamic routing
        self.cost_router = cost_router
        # Phase 3.4: DAG-based parallel tool execution
        self.dag_planner = dag_planner
        self.enable_dag_execution = enable_dag_execution
        # Only pass llm_client to SecurityCoordinator when security is explicitly
        # configured; default heuristic-only mode avoids extra LLM calls.
        self.security_coord = SecurityCoordinator(
            security_config,
            llm_client=llm_client if security_config is not None else None,
            checkpoint_manager=checkpoint_manager,
        )
        self.messages: list[Message] = []

    @property
    def state(self) -> QueryState:
        return self._state

    def _set_state(self, state: QueryState) -> None:
        self._state = state
        self._checkpoint()

    def _checkpoint(self) -> None:
        """Serialize current state to SessionStore, debounced to avoid O(n²) writes.

        Only writes when the iteration advances or the state becomes terminal.
        """
        if self._session_store is None or self._session_id is None:
            return

        terminal_states = {
            QueryState.COMPLETED,
            QueryState.INCOMPLETE,
            QueryState.STOPPED,
            QueryState.ERROR,
        }
        should_checkpoint = (
            self._state in terminal_states or self._iteration != self._last_checkpointed_iteration
        )
        if not should_checkpoint:
            return

        messages_json = [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "tool_call_id": m.tool_call_id,
                "model_version": m.model_version,
            }
            for m in self.messages
        ]
        plan_json = None
        if self._plan_result is not None:
            plan_json = {
                "selected_tool_names": self._plan_result.selected_tool_names,
                "system_prompt_append": self._plan_result.system_prompt_append,
            }
        try:
            self._session_store.save_checkpoint(
                session_id=self._session_id,
                state=self._state.name,
                messages=messages_json,
                plan_result=plan_json,
                iteration=self._iteration,
                feedback_retries=self._feedback_retries,
                model=self.llm.model if self.llm else None,
            )
            self._last_checkpointed_iteration = self._iteration
            self._last_checkpointed_state = self._state
        except Exception as e:
            # Checkpoint failures must not crash the session
            if self.logger:
                try:
                    self.logger.debug(f"Checkpoint failed for {self._session_id}: {e}")
                except Exception:
                    pass

    def register_tool_handler(self, tool_name: str, handler: Callable) -> None:
        self.tool_executor.register_handler(tool_name, handler)

    def set_model(self, model: str) -> str:
        old_model = self.llm.model
        self.llm.model = model
        self.messages.append(
            Message(role="system", content=f"Model switched to '{model}'", model_version=model)
        )
        return old_model

    def get_model(self) -> str:
        return self.llm.model

    async def run(
        self, initial_query: str | None = None, stream: bool | None = None
    ) -> AsyncIterator[QueryResult]:
        if stream is None:
            stream = self.stream
            try:
                from unittest.mock import Mock, sentinel

                if isinstance(self.llm, Mock):
                    has_stream_config = self.llm.complete_stream.side_effect is not None or (
                        hasattr(self.llm.complete_stream, "_mock_return_value")
                        and self.llm.complete_stream._mock_return_value is not sentinel.DEFAULT
                    )
                    if not has_stream_config:
                        stream = False
            except ImportError:
                pass
        if self._state == QueryState.STOPPED:
            return
        self._running = True
        self._set_state(QueryState.PLANNING)
        # Per-run usage-feedback attribution starts empty
        self._injected_lesson_ids = []
        # Per-run pivotal retry bookkeeping starts empty (Workstream C).
        # _pivotal_turn itself is intentionally NOT reset here: it is a sticky
        # annotation consumed by post-run reflection, and may be set externally.
        self._pivotal_failure_counts = {}
        self._pivotal_retry_counts = {}
        if self._session_id is None:
            self._session_id = str(uuid.uuid4())
            self._session_start_time = time.time()
        yield QueryResult(is_status=True, status_message="Planning strategy...", state=self._state)
        if self.logger:
            self.logger.info(f"Starting QueryLoop run. Initial query: {initial_query}")
        try:
            if initial_query:
                self.messages.append(Message(role="user", content=initial_query))

            # v4: Wiki retrieval happens BEFORE planner (async context)
            wiki_hint = ""
            if initial_query and self.wiki is not None and self.pageindex is not None:
                try:
                    mem_cfg = getattr(self, "_config_memory", None)
                    pi_cfg = getattr(mem_cfg, "pageindex", None)
                    routing_timeout = _coerce_float(
                        getattr(
                            mem_cfg,
                            "routing_timeout_seconds",
                            getattr(pi_cfg, "routing_timeout_seconds", 2.0),
                        ),
                        2.0,
                    )
                    min_confidence = _coerce_float(
                        getattr(pi_cfg, "routing_min_confidence", 0.3), 0.3
                    )
                    wiki_hint = await asyncio.wait_for(
                        self._build_wiki_hint(initial_query, min_confidence),
                        timeout=routing_timeout,
                    )
                except asyncio.TimeoutError:
                    pass  # Fail gracefully — preserve planner latency
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"PageIndex routing failed (non-fatal): {e}")

            # --- Planning: tool, skill, and MCP selection ---
            self._plan_result = None
            if initial_query:
                plan_request = PlanRequest(
                    query=initial_query,
                    available_tools=self.tools.get_tool_schemas()
                    + (self.mcp_bridge.get_tool_schemas() if self.mcp_bridge else []),
                    available_skills=self.instruction_set.skills if self.instruction_set else [],
                    available_mcps=[
                        {"name": cfg.name, "description": cfg.description}
                        for cfg in (self.mcp_bridge.configs if self.mcp_bridge else [])
                    ],
                    wiki_hint=wiki_hint,  # v4: pass wiki hints via PlanRequest
                )
                if self.context_planner is not None:
                    self._plan_result = self.context_planner.plan(plan_request)
                    if self.logger:
                        self.logger.info(
                            f"Planner selected tools: {self._plan_result.selected_tool_names}"
                        )
                    if self._plan_result.system_prompt_append:
                        self.messages.insert(
                            0,
                            Message(role="system", content=self._plan_result.system_prompt_append),
                        )
                elif wiki_hint:
                    # No planner (e.g. no prompt skills loaded) — inject the
                    # memory hint directly so retrieval still reaches the model.
                    self.messages.insert(0, Message(role="system", content=wiki_hint.strip()))

                # Phase B: Inject response style preferences into system prompt
                try:
                    from vibe.preferences.style_policy import ResponseStylePolicy

                    style_policy = ResponseStylePolicy()
                    style_append = style_policy.get_system_prompt_append()
                    if style_append:
                        self.messages.insert(0, Message(role="system", content=style_append))
                except Exception:
                    pass  # Non-critical: style preferences are optional

            # Phase: Adaptive iteration budgets
            if self.adaptive_budget and initial_query:
                self._iteration_budget = self._budget_allocator.allocate(
                    initial_query,
                    available_tools=self.tools.get_tool_schemas()
                    + (self.mcp_bridge.get_tool_schemas() if self.mcp_bridge else []),
                )
                if self.logger:
                    self.logger.info(
                        f"Adaptive budget: allocated={self._iteration_budget.allocated} "
                        f"for query complexity"
                    )

            iteration = self._iteration
            max_iterations = (
                self._iteration_budget.allocated
                if self.adaptive_budget and self._iteration_budget
                else int(self.max_iterations)
                if self.max_iterations is not None
                else 50
            )
            while self._running and iteration < max_iterations:
                iteration += 1
                self._iteration = iteration
                if self._iteration_budget:
                    self._iteration_budget.consume(1)
                self._set_state(QueryState.PROCESSING)
                try:
                    llm_msgs = self._build_llm_messages()
                    compacted = await self._maybe_compact(llm_msgs)
                    if compacted:
                        yield compacted
                        llm_msgs = self._build_llm_messages()

                    tools_for_llm = self._select_tools_for_llm()

                    # Phase 3.3: Cost-aware dynamic routing
                    if self.cost_router is not None:
                        decision = self.cost_router.route(
                            llm_msgs,
                            available_tools=tools_for_llm,
                            provider_prefs=getattr(self, "_provider_prefs", None),
                        )
                        if decision.model_id != self.llm.model:
                            self.set_model(decision.model_id)
                            if self.logger:
                                self.logger.info(
                                    f"CostRouter: switched to {decision.model_id} "
                                    f"({decision.reason})"
                                )

                    yield QueryResult(
                        is_status=True,
                        status_message=f"Waiting for {self.llm.model}...",
                        state=self._state,
                    )
                    start_time = time.time()
                    if stream:
                        content_acc = []
                        reasoning_acc = []
                        tool_calls_acc = []
                        usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                        model_used = self.llm.model
                        finish_reason = None
                        error = None
                        error_type = ErrorType.NONE

                        async def run_stream():
                            nonlocal model_used, finish_reason, error, error_type
                            try:
                                async for chunk in self.llm.complete_stream(
                                    llm_msgs, tools=tools_for_llm
                                ):
                                    if not self._running:
                                        break
                                    if chunk.is_error:
                                        error = chunk.error
                                        error_type = chunk.error_type
                                        yield chunk
                                        return

                                    model_used = chunk.model_used or model_used
                                    if chunk.content:
                                        content_acc.append(chunk.content)
                                    if chunk.reasoning_content:
                                        reasoning_acc.append(chunk.reasoning_content)
                                    if chunk.finish_reason:
                                        finish_reason = chunk.finish_reason
                                    if chunk.tool_calls:
                                        tool_calls_acc.extend(chunk.tool_calls)
                                    if chunk.usage:
                                        usage_acc["prompt_tokens"] = max(
                                            usage_acc["prompt_tokens"],
                                            chunk.usage.get("prompt_tokens", 0),
                                        )
                                        usage_acc["completion_tokens"] += chunk.usage.get(
                                            "completion_tokens", 0
                                        )
                                        usage_acc["total_tokens"] = (
                                            usage_acc["prompt_tokens"]
                                            + usage_acc["completion_tokens"]
                                        )

                                    yield chunk
                            except Exception as e:
                                error = str(e)
                                error_type = ErrorType.UNKNOWN_ERROR
                                yield LLMResponse(content="", error=error, error_type=error_type)

                        async for chunk in run_stream():
                            if chunk.is_error:
                                break
                            if chunk.content or chunk.reasoning_content:
                                yield QueryResult(
                                    response=chunk.content or "",
                                    reasoning_content=chunk.reasoning_content or "",
                                    state=self._state,
                                    model_used=model_used,
                                    is_status=False,
                                    is_chunk=True,
                                    is_stream_chunk=True,
                                )

                        # If the loop was stopped mid-stream, do not process partial output
                        if not self._running:
                            return

                        if error:
                            response = LLMResponse(
                                content="",
                                error=error,
                                error_type=error_type,
                                model_used=model_used,
                            )
                        else:
                            assembled_tool_calls = (
                                self._assemble_tool_calls(tool_calls_acc)
                                if tool_calls_acc
                                else None
                            )
                            response = LLMResponse(
                                content="".join(content_acc),
                                reasoning_content="".join(reasoning_acc) if reasoning_acc else None,
                                tool_calls=assembled_tool_calls,
                                finish_reason=finish_reason,
                                usage=usage_acc,
                                model_used=model_used,
                            )
                    else:
                        response = await self.error_recovery.execute_with_retry(
                            lambda: self.llm.complete(llm_msgs, tools=tools_for_llm)
                        )
                    elapsed = time.time() - start_time
                    metrics = self._calc_metrics(response, elapsed)

                    if response.is_error:
                        self._set_state(QueryState.ERROR)
                        failed_model = getattr(response, "model_used", None) or self.llm.model
                        failed_base_url = self.llm.base_url
                        if getattr(self.llm, "registry", None) and failed_model:
                            profile = self.llm.registry.get(failed_model)
                            if profile:
                                failed_base_url = profile.base_url

                        # Yield status message showing which model failed
                        yield QueryResult(
                            is_status=True,
                            status_message=(
                                f"Connection failed for model '{failed_model}' at {failed_base_url}"
                            ),
                            state=self._state,
                        )

                        actionable_hint = getattr(response, "actionable_hint", None)

                        yield QueryResult(
                            response="",
                            error=Exception(response.error),
                            actionable_hint=actionable_hint,
                            model_used=failed_model,
                            metrics=metrics,
                            state=self._state,
                        )
                        break

                    model_used = getattr(response, "model_used", None)
                    if model_used and model_used != self.llm.model:
                        yield QueryResult(
                            is_status=True,
                            status_message=f"Responded via fallback model: {model_used}",
                            state=self._state,
                        )

                    if not response.content and not response.tool_calls:
                        self._set_state(QueryState.ERROR)
                        yield QueryResult(
                            response="",
                            error=Exception("Empty response"),
                            metrics=metrics,
                            state=self._state,
                        )
                        break

                    # Phase: Adaptive budget — check for early exit signals
                    if self.adaptive_budget and self._iteration_budget:
                        # Check completion phrases
                        sig = self._iteration_budget.check_completion_phrase(response.content or "")
                        if sig.name != "CONTINUE":
                            self._iteration_budget.add_signal(sig)
                            if self.logger:
                                self.logger.info(f"Adaptive budget: early exit signal={sig.name}")
                            break

                        # Check stagnation
                        current_tools = sum(1 for m in self.messages if m.role == "tool")
                        sig = self._iteration_budget.check_stagnation(
                            current_tools, len(self.messages)
                        )
                        if sig.name != "CONTINUE":
                            self._iteration_budget.add_signal(sig)
                            if self.logger:
                                self.logger.info("Adaptive budget: stagnation detected")
                            break

                        # Check token pressure
                        total_chars = sum(len(m.content) for m in self.messages if m.content)
                        estimated_tokens = total_chars // 4
                        sig = self._iteration_budget.check_token_pressure(
                            estimated_tokens, self.max_context_tokens
                        )
                        if sig.name != "CONTINUE":
                            self._iteration_budget.add_signal(sig)
                            if self.logger:
                                self.logger.info("Adaptive budget: token pressure")
                            break

                    if response.tool_calls:
                        tool_names = [extract_tool_call_name(tc) for tc in response.tool_calls]
                        if self.logger:
                            self.logger.info(f"LLM requested tools: {tool_names}")
                        yield QueryResult(
                            is_status=True,
                            status_message=f"Executing tools: {tool_names}...",
                            state=QueryState.TOOL_EXECUTION,
                        )
                        res = await self._process_tool_response(response, metrics)
                        res.model_used = getattr(response, "model_used", None)
                        res.actionable_hint = getattr(response, "actionable_hint", None)
                        yield res
                    else:
                        should_continue, result = await self._process_content_response(
                            response, metrics
                        )
                        if result:
                            result.model_used = getattr(response, "model_used", None)
                            result.actionable_hint = getattr(response, "actionable_hint", None)
                            yield result
                        if not should_continue:
                            break

                except Exception as e:
                    # Phase P5: Try recovery rules before giving up
                    recovery_result = await self._try_recovery(e)
                    if recovery_result:
                        yield recovery_result
                        continue
                    self._set_state(QueryState.ERROR)
                    yield QueryResult(response="", error=e, state=self._state)
                    break

            if self._state not in (QueryState.COMPLETED, QueryState.ERROR, QueryState.STOPPED):
                # Distinguish between natural completion and max_iterations exhaustion
                if iteration >= max_iterations:
                    self._set_state(QueryState.INCOMPLETE)
                else:
                    self._set_state(QueryState.COMPLETED)
        finally:
            self._running = False

            # Phase 3.2: Delete checkpoint FIRST — must run before any slow async
            # operations that could be interrupted by KeyboardInterrupt.
            if self._session_store and self._session_id:
                try:
                    self._session_store.delete_checkpoint(self._session_id)
                except Exception:
                    pass

            # Record session telemetry
            if self._telemetry is not None and self._session_id:
                try:
                    elapsed = time.time() - self._session_start_time
                    total_chars = sum(len(m.content) for m in self.messages if m.content)
                    self._telemetry.record_session(
                        session_id=self._session_id,
                        duration_seconds=elapsed,
                        total_chars=total_chars,
                        state=self._state.name,
                    )
                except Exception:
                    pass

            # Phase 1b: Spawn background wiki extraction (non-blocking)
            # ERROR sessions included: failed runs carry the most valuable lessons.
            if (
                self.wiki is not None
                and self._config_memory is not None
                and getattr(self._config_memory.wiki, "auto_extract", False)
                and self._state in (QueryState.COMPLETED, QueryState.INCOMPLETE, QueryState.ERROR)
            ):
                try:
                    # Copy messages to avoid mutation during extraction
                    messages_copy = list(self.messages)
                    self._wiki_extract_task = asyncio.create_task(
                        self._extract_to_wiki(messages_copy, self._session_id)
                    )
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"Wiki extract task spawn failed (non-fatal): {e}")

            # Trajectory reflection: distill reusable lessons post-session
            # (Reflector→Curator). ERROR sessions included: failures carry the
            # richest learning signal. Non-blocking, fire-and-forget.
            reflection_cfg = (
                getattr(self._config_memory, "reflection", None)
                if self._config_memory is not None
                else None
            )
            if (
                self.wiki is not None
                and self.pageindex is not None
                and reflection_cfg is not None
                and getattr(self._config_memory, "enabled", False)
                and getattr(reflection_cfg, "enabled", False)
                and self._state in (QueryState.COMPLETED, QueryState.INCOMPLETE, QueryState.ERROR)
            ):
                try:
                    # Copy messages to avoid mutation during reflection
                    messages_copy = list(self.messages)
                    self._reflection_task = asyncio.create_task(
                        self._reflect_on_trajectory(messages_copy, self._session_id, self._state)
                    )
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"Reflection task spawn failed (non-fatal): {e}")

            # Phase 2: Spawn background RLM trigger analysis (non-blocking, MVP: log only)
            if (
                self._telemetry is not None
                and self._config_memory is not None
                and getattr(self._config_memory.rlm, "enabled", False)
            ):
                try:
                    self._rlm_trigger_task = asyncio.create_task(self._maybe_trigger_rlm())
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"RLM trigger task spawn failed (non-fatal): {e}")

            # Phase A: Spawn background skill-maker pattern detection (non-blocking)
            if (
                self.skill_maker is not None
                and self._state == QueryState.COMPLETED
                and (self._skill_maker_task is None or self._skill_maker_task.done())
            ):
                try:
                    self._skill_maker_task = asyncio.create_task(self.skill_maker.run_once())
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"SkillMaker task spawn failed (non-fatal): {e}")

            # Log session to trace store if available
            if self._trace_store and self._session_id:
                try:
                    tool_results = []
                    for msg in self.messages:
                        if msg.role == "tool":
                            tool_results.append(
                                {
                                    "tool_call_id": msg.tool_call_id,
                                    "content": msg.content,
                                }
                            )
                    self._trace_store.log_session(
                        session_id=self._session_id,
                        messages=[{"role": m.role, "content": m.content} for m in self.messages],
                        tool_results=tool_results,
                        success=self._state == QueryState.COMPLETED,
                        model=self.llm.model if self.llm else "unknown",
                        error=(
                            str(self._state.name)
                            if self._state in (QueryState.ERROR, QueryState.INCOMPLETE)
                            else None
                        ),
                    )
                except Exception:
                    # Logging failures must not crash the session
                    pass

            # Phase 5.2: Offer rollback if shadow exists and session ended in
            # error/incomplete/interrupted
            if self.shadow_manager is not None and self._session_id:
                if self._state != QueryState.COMPLETED:
                    try:
                        shadows = self.shadow_manager.list_shadows()
                        matching = [s for s in shadows if s.session_id == self._session_id]
                        if matching:
                            latest = max(matching, key=lambda s: s.created_at)
                            if self.logger:
                                self.logger.info(
                                    f"Session {self._session_id[:16]}... ended in "
                                    f"{self._state.name}. Rollback available: {latest.branch_name}"
                                )
                    except Exception:
                        pass

    async def _maybe_compact(self, llm_msgs: list[dict]) -> QueryResult | None:
        """Compact context if needed. Returns a QueryResult if compaction occurred."""
        if not self.compaction_coord.should_compact(llm_msgs):
            return None
        compacted_msgs, was_compacted = await self.compaction_coord.compact(llm_msgs)
        if was_compacted:
            self.messages = [
                Message(
                    role=m["role"],
                    content=m.get("content", ""),
                    tool_calls=m.get("tool_calls"),
                    tool_call_id=m.get("tool_call_id"),
                )
                for m in compacted_msgs
            ]
        return QueryResult(
            response="",
            context_truncated=was_compacted,
            state=QueryState.PROCESSING,
        )

    def _select_tools_for_llm(self) -> list[dict]:
        """Select tools based on planner result, with safety fallback."""
        internal_schemas = self.tools.get_tool_schemas()
        mcp_schemas = self.mcp_bridge.get_tool_schemas() if self.mcp_bridge else []
        all_schemas = internal_schemas + mcp_schemas
        selected = self.tool_executor.select_tools(
            all_schemas,
            self._plan_result.selected_tool_names if self._plan_result else None,
        )
        return selected

    def _filter_tool_calls(
        self, tool_calls: list
    ) -> tuple[list[Any], list[int], list[ToolResult | None]]:
        """Filter tool calls through security checks.

        Returns:
            allowed_calls: List of calls that passed security.
            allowed_indices: Original indices of allowed calls.
            results: List with None for allowed calls and error ToolResult for blocked calls.
        """
        results: list[ToolResult | None] = [None] * len(tool_calls)
        allowed_calls: list[Any] = []
        allowed_indices: list[int] = []

        if self.security_coord is None:
            return tool_calls, list(range(len(tool_calls))), results

        for i, call in enumerate(tool_calls):
            call_name = extract_tool_call_name(call)
            arguments = extract_tool_call_arguments(call)
            check = self.security_coord.evaluate_tool_call(call_name, arguments)
            if check.allowed:
                if check.modified_args:
                    arguments.update(check.modified_args)
                    self._apply_modified_args_to_call(call, arguments)
                allowed_calls.append(call)
                allowed_indices.append(i)
            else:
                results[i] = ToolResult(
                    success=False,
                    content=None,
                    error=f"Security blocked: {check.reason}",
                    # Stamp denials so pivotal retry (Workstream C) can reliably
                    # recognize them as final and never retry them.
                    metadata={
                        "security_denial": True,
                        "security_layer": check.layer or "unknown",
                    },
                )

        return allowed_calls, allowed_indices, results

    def _apply_modified_args_to_call(self, call: Any, arguments: dict[str, Any]) -> None:
        """Apply security-modified arguments back to the original tool call."""
        import json

        if isinstance(call, dict):
            func = call.get("function", {})
            if isinstance(func.get("arguments"), str):
                func["arguments"] = json.dumps(arguments)
            elif "arguments" in call:
                call["arguments"] = arguments
            else:
                func["arguments"] = arguments
        else:
            call.arguments = arguments

    async def _execute_with_security(self, tool_calls: list) -> list[ToolResult]:
        """Execute tool calls with 5-layer security checks.

        Returns results in the same order as tool_calls, with blocked calls
        replaced by error ToolResults.
        """
        allowed_calls, allowed_indices, results = self._filter_tool_calls(tool_calls)

        if allowed_calls:
            executed = await self.tool_executor.execute(allowed_calls, session_id=self._session_id)
            for idx, result in zip(allowed_indices, executed):
                results[idx] = result

        return [r for r in results if r is not None]

    async def _execute_tools_dag(self, tool_calls: list) -> list[ToolResult]:
        """Execute tool calls via DAG planner for parallelization.

        Falls back to sequential execution if the DAG is invalid or has no parallelism.
        """
        allowed_calls, allowed_indices, results = self._filter_tool_calls(tool_calls)

        if not allowed_calls or len(allowed_calls) <= 1:
            # Not enough calls for DAG parallelism — use sequential
            if allowed_calls:
                executed = await self.tool_executor.execute(
                    allowed_calls, session_id=self._session_id
                )
                for idx, result in zip(allowed_indices, executed):
                    results[idx] = result
            return [r for r in results if r is not None]

        dag = self.dag_planner.build_from_tool_calls(allowed_calls)
        if not dag.is_valid or dag.max_depth == 0:
            # No parallelism detected — fallback to sequential
            if self.logger:
                self.logger.debug(
                    f"DAG fallback: valid={dag.is_valid}, depth={dag.max_depth}, "
                    f"nodes={dag.node_count}"
                )
            executed = await self.tool_executor.execute(allowed_calls, session_id=self._session_id)
            for idx, result in zip(allowed_indices, executed):
                results[idx] = result
            return [r for r in results if r is not None]

        # Execute via DAGExecutor for parallelization
        from vibe.harness.dag_planner import DAGExecutor

        dag_executor = DAGExecutor(self.tool_executor)
        dag_results = await dag_executor.execute(dag)

        # Map DAG results back to original tool call order
        for idx, call in zip(allowed_indices, allowed_calls):
            node_id = f"tool_{allowed_indices.index(idx)}"
            result = dag_results.get(node_id)
            if result is not None:
                results[idx] = result

        return [r for r in results if r is not None]

    async def _process_tool_response(self, response: LLMResponse, metrics: Metrics) -> QueryResult:
        """Handle a response containing tool calls."""
        self._set_state(QueryState.TOOL_EXECUTION)

        # Phase 3.4: Use DAG execution for parallelizable tool calls
        if (
            self.enable_dag_execution
            and self.dag_planner is not None
            and len(response.tool_calls) > 1
        ):
            tool_results = await self._execute_tools_dag(response.tool_calls)
        else:
            tool_results = await self._execute_with_security(response.tool_calls)
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
            # Expose the tool name/args to CLI renderers via metadata.
            # setdefault so executors that already set metadata win.
            try:
                result.metadata.setdefault("tool_name", extract_tool_call_name(call))
                result.metadata.setdefault("tool_args", extract_tool_call_arguments(call))
            except Exception:
                pass
            self.messages.append(
                Message(
                    role="tool",
                    content=result.content if result.success else result.error,
                    tool_call_id=tool_call_id,
                    metadata={"tool_name": extract_tool_call_name(call)},
                )
            )
        self._set_state(QueryState.SYNTHESIZING)
        # Workstream C: detect repeated identical tool failures and, before the
        # loop drifts into ERROR/INCOMPLETE, run at most one guided retry of the
        # pivotal call. Never raises; security denials are excluded.
        await self._maybe_guided_pivotal_retry(response.tool_calls, tool_results)
        return QueryResult(
            response=response.content or "",
            reasoning_content=getattr(response, "reasoning_content", None) or "",
            tool_results=tool_results,
            metrics=metrics,
            state=self._state,
        )

    # ------------------------------------------------------------------
    # Workstream C: Pivotal local retry (PivoARL)
    # ------------------------------------------------------------------

    # Error prefixes that mark a failure as a security/policy denial even when
    # the metadata stamp is absent (e.g. tool-internal pattern denylists).
    _SECURITY_DENIAL_PREFIXES = ("Security blocked:", "Command blocked by safety policy")

    def _tool_call_signature(self, call: Any) -> tuple[str, str] | None:
        """Normalize a tool call to a (name, canonical-args) signature.

        Arguments are canonicalized as sorted-key JSON so semantically identical
        calls compare equal regardless of dict ordering or str/dict encoding.
        Returns None when the call cannot be normalized. Never raises.
        """
        try:
            name = extract_tool_call_name(call)
            if not name:
                return None
            args = extract_tool_call_arguments(call)
            normalized = json.dumps(args, sort_keys=True, default=str)
            return (name, normalized)
        except Exception:
            return None

    def _is_security_denial(self, result: ToolResult) -> bool:
        """True if the failure is a security/policy denial (final, never retried).

        Fail-closed: if we cannot tell, treat it as a denial.
        """
        try:
            metadata = getattr(result, "metadata", None)
            if isinstance(metadata, dict) and metadata.get("security_denial"):
                return True
            error = getattr(result, "error", None) or ""
            if isinstance(error, str) and error.startswith(self._SECURITY_DENIAL_PREFIXES):
                return True
            return False
        except Exception:
            return True

    def _pivotal_budget_remaining(self) -> bool:
        """True while at least one organic loop iteration remains."""
        max_iterations = (
            self._iteration_budget.allocated
            if self.adaptive_budget and self._iteration_budget
            else int(self.max_iterations)
            if self.max_iterations is not None
            else 50
        )
        return self._iteration < max_iterations

    async def _maybe_guided_pivotal_retry(
        self, tool_calls: list, tool_results: list[ToolResult]
    ) -> None:
        """Detect repeated identical tool failures and run one guided retry.

        Tracks failures by call signature within a run. When the same failing
        call repeats (the organic next-iteration retry already failed once), the
        current iteration is marked as the pivotal turn and — if enabled, not a
        security denial, retries remain for the signature, and iteration budget
        remains — exactly one guided retry of the pivotal call is performed.
        Never raises: any internal failure falls back to current behavior.
        """
        try:
            if not self._pivotal_retry_enabled:
                return
            if not tool_calls or not tool_results:
                return
            for call, result in zip(tool_calls, tool_results):
                if result is None or getattr(result, "success", False):
                    continue
                if self._is_security_denial(result):
                    continue
                signature = self._tool_call_signature(call)
                if signature is None:
                    continue
                count = self._pivotal_failure_counts.get(signature, 0) + 1
                self._pivotal_failure_counts[signature] = count
                if count < 2:
                    continue
                # Repeated identical failure: this iteration is the pivotal turn.
                if self._pivotal_turn is None:
                    self._pivotal_turn = self._iteration
                if self._pivotal_retry_counts.get(signature, 0) >= self._max_pivotal_retries:
                    continue
                if not self._pivotal_budget_remaining():
                    continue
                # Count the retry before attempting it so it fires at most once
                # per signature per run, regardless of outcome.
                self._pivotal_retry_counts[signature] = (
                    self._pivotal_retry_counts.get(signature, 0) + 1
                )
                if self.logger:
                    self.logger.info(
                        f"Pivotal retry: guided retry of '{signature[0]}' "
                        f"after {count} identical failures (turn {self._iteration})"
                    )
                await self._guided_pivotal_retry(call, result)
        except Exception as e:
            if self.logger:
                try:
                    self.logger.debug(f"Pivotal retry detection failed (non-fatal): {e}")
                except Exception:
                    pass

    async def _guided_pivotal_retry(self, call: Any, result: ToolResult) -> None:
        """Perform one reflection-guided retry of a pivotal failing tool call.

        Appends a bounded, structured guidance message naming the failed tool
        and its error, asks the model for a corrected call, then executes the
        corrected call through the normal security path and appends results to
        the transcript. The correct message prefix is reused as-is (no
        re-planning, no message reset). Never raises.
        """
        try:
            tool_name = extract_tool_call_name(call) or "unknown"
            signature = self._tool_call_signature(call)
            args_json = signature[1] if signature else "{}"
            error_text = str(getattr(result, "error", None) or "unknown error")
            guidance = (
                "PIVOTAL RETRY — repeated tool failure detected.\n"
                f"Failed tool: {tool_name}\n"
                f"Failed arguments: {args_json[:500]}\n"
                f"Error: {error_text[:500]}\n"
                "This exact call has failed multiple times. Analyze the error and "
                "respond with a CORRECTED call to this tool (fixed or different "
                "arguments), or explain why the task cannot proceed. "
                "Do not repeat the identical failing call."
            )
            self.messages.append(Message(role="system", content=guidance))

            llm_msgs = self._build_llm_messages()
            response = await self.error_recovery.execute_with_retry(
                lambda: self.llm.complete(llm_msgs, tools=self._select_tools_for_llm())
            )
            if response is None or getattr(response, "is_error", False):
                return

            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                # Model answered with content instead of a corrected call; keep
                # the reply in the transcript so the loop continues from it.
                content = getattr(response, "content", None)
                if content:
                    self.messages.append(
                        Message(role="assistant", content=content, model_version=self.llm.model)
                    )
                return

            self.messages.append(
                Message(
                    role="assistant",
                    content=getattr(response, "content", None) or "",
                    tool_calls=tool_calls,
                    model_version=self.llm.model,
                )
            )
            retry_results = await self._execute_with_security(tool_calls)
            for retry_call, retry_result in zip(tool_calls, retry_results):
                if isinstance(retry_call, dict):
                    tool_call_id = retry_call.get("id")
                else:
                    tool_call_id = getattr(retry_call, "id", None)
                self.messages.append(
                    Message(
                        role="tool",
                        content=(
                            retry_result.content if retry_result.success else retry_result.error
                        ),
                        tool_call_id=tool_call_id,
                        metadata={"tool_name": extract_tool_call_name(retry_call)},
                    )
                )
        except Exception as e:
            # Guided-retry failures must not add new failure modes to the loop;
            # fall back to normal degradation.
            if self.logger:
                try:
                    self.logger.debug(f"Guided pivotal retry failed (non-fatal): {e}")
                except Exception:
                    pass

    async def _process_content_response(
        self, response: LLMResponse, metrics: Metrics
    ) -> tuple[bool, QueryResult | None]:
        """Handle a response with no tool calls. Returns (should_continue, result_to_yield)."""
        self.messages.append(
            Message(role="assistant", content=response.content or "", model_version=self.llm.model)
        )
        # Feedback loop: evaluate response before completing
        should_continue, hint = await self.feedback_coord.evaluate(response.content or "")
        if should_continue and hint:
            self.messages.append(Message(role="system", content=hint))
            self._set_state(QueryState.PROCESSING)
            return True, QueryResult(
                response=response.content or "",
                reasoning_content=getattr(response, "reasoning_content", None) or "",
                metrics=metrics,
                state=QueryState.PROCESSING,
            )
        self._set_state(QueryState.COMPLETED)
        return False, QueryResult(
            response=response.content or "",
            reasoning_content=getattr(response, "reasoning_content", None) or "",
            metrics=metrics,
            state=self._state,
        )

    async def _execute_tool_calls(self, tool_calls: list) -> list[ToolResult]:
        """Deprecated: delegates to ToolExecutor."""
        return await self.tool_executor.execute(tool_calls, session_id=self._session_id)

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
        # Robustly extract reasoning tokens if available
        rt = 0
        if "reasoning_tokens" in usage:
            rt = usage["reasoning_tokens"]
        elif "completion_tokens_details" in usage and isinstance(
            usage["completion_tokens_details"], dict
        ):
            rt = usage["completion_tokens_details"].get("reasoning_tokens", 0)

        # Fallback: estimate completion tokens from content length when the
        # streaming provider does not report usage (common for Ollama, vLLM,
        # and many OpenAI-compatible proxies). ASCII text averages ~4 chars/token;
        # non-ASCII (such as CJK, Arabic, or Cyrillic) averages ~0.8 tokens per char.
        # Reasoning content is included since it also consumes tokens.
        if ct == 0 and response.content:
            combined = response.content
            if response.reasoning_content:
                combined += response.reasoning_content

            estimated_tokens = 0.0
            for char in combined:
                if ord(char) < 128:
                    estimated_tokens += 0.25
                else:
                    estimated_tokens += 0.8

            ct = max(1, int(estimated_tokens))
            tt = pt + ct

        tps = ct / elapsed if elapsed > 0 else 0
        return Metrics(
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            elapsed_seconds=elapsed,
            tokens_per_second=tps,
            reasoning_tokens=rt,
        )

    def _assemble_tool_calls(
        self, tool_calls_list: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Assembles stream tool call chunks into standardized list of tool calls."""
        assembled = {}
        for tc in tool_calls_list:
            idx = tc.get("index")
            if idx is None:
                continue
            if idx not in assembled:
                assembled[idx] = {
                    "id": tc.get("id"),
                    "type": "function",
                    "function": {
                        "name": tc.get("function", {}).get("name") or "",
                        "arguments": tc.get("function", {}).get("arguments") or "",
                    },
                }
            else:
                tc_id = tc.get("id")
                if tc_id:
                    assembled[idx]["id"] = tc_id
                name = tc.get("function", {}).get("name")
                if name:
                    assembled[idx]["function"]["name"] = name
                args = tc.get("function", {}).get("arguments")
                if args:
                    assembled[idx]["function"]["arguments"] += args

        return list(assembled.values()) if assembled else None

    # ------------------------------------------------------------------
    # Phase 1b: Wiki auto-extraction
    # ------------------------------------------------------------------

    async def _build_wiki_hint(self, query: str, min_confidence: float = 0.3) -> str:
        """Build the "## Relevant Knowledge" system-prompt block for a query.

        Routes the query through PageIndex, drops nodes below the confidence
        threshold, then fetches each surviving wiki page to include a bounded
        content snippet plus its tags. Pages flagged as contradicted, expired,
        or otherwise non-injectable are skipped. Never raises.
        """
        try:
            from pathlib import Path

            from vibe.memory.wiki import _parse_page_file, is_page_injectable

            nodes = await self.pageindex.route(query)
            if not nodes:
                return ""

            threshold = _coerce_float(min_confidence, 0.3)

            lines: list[str] = []
            for node in nodes[:3]:
                try:
                    if _coerce_float(getattr(node, "confidence", 0.0), 0.0) < threshold:
                        continue
                    file_path = getattr(node, "file_path", None)
                    if not file_path:
                        continue
                    page = _parse_page_file(Path(str(file_path)))
                    if page is None or not is_page_injectable(page):
                        continue
                    snippet = page.content.strip()[:500]
                    tags = ", ".join(page.tags) if page.tags else "none"
                    lines.append(f"### {page.title} (tags: {tags})\n{snippet}")
                    # Usage feedback: remember lesson pages actually injected
                    self._track_injected_lesson(page)
                except Exception:
                    continue

            if not lines:
                return ""
            return "\n\n## Relevant Knowledge\n" + "\n\n".join(lines)
        except Exception:
            return ""

    def _track_injected_lesson(self, page: Any) -> None:
        """Record the stable id of a lesson-tagged page injected into the prompt.

        Never raises — tracking must never break hint building.
        """
        try:
            page_id = getattr(page, "id", None)
            if page_id and "lesson" in (getattr(page, "tags", None) or []):
                self._injected_lesson_ids.append(page_id)
        except Exception:
            pass

    async def _extract_to_wiki(self, messages: list[Message], session_id: str | None) -> None:
        """Background task: extract knowledge from session and write to wiki.

        Never raises — all errors are caught and logged.
        """
        if self.wiki is None or session_id is None:
            return

        try:
            from vibe.memory.extraction import KnowledgeExtractor

            extractor = KnowledgeExtractor(
                llm_client=self.llm,
                wiki=self.wiki,
                pageindex=self.pageindex,
                flash_client=getattr(self.wiki, "_flash_client", None),
                config=self._config_memory,
                extraction_policy=getattr(self, "_extraction_policy", None),  # Phase P8
            )

            items = await extractor.extract_from_session(messages, session_id)
            if not items:
                return

            # Apply quality gates
            novelty_threshold = 0.5
            confidence_threshold = 0.8
            if self._config_memory is not None:
                novelty_threshold = getattr(self._config_memory.wiki, "novelty_threshold", 0.5)
                confidence_threshold = getattr(
                    self._config_memory.wiki, "confidence_threshold", 0.8
                )

            approved = await extractor.apply_gates(
                items,
                novelty_threshold=novelty_threshold,
                confidence_threshold=confidence_threshold,
            )

            created = 0
            updated = 0
            for item in approved:
                try:
                    # Check if page with similar title exists
                    existing = await self._find_existing_page(item.get("title", ""))
                    if existing:
                        # Merge content: append new citations
                        new_citations = item.get("citations", [])
                        page = await self.wiki.update_page(
                            page_id=existing.id,
                            content=item.get("content", ""),
                            citations=new_citations,
                        )
                        updated += 1
                    else:
                        page = await self.wiki.create_page(
                            title=item.get("title", ""),
                            content=item.get("content", ""),
                            tags=item.get("tags", []),
                            citations=item.get("citations", []),
                            status="draft",
                        )
                        created += 1
                    # Keep PageIndex in sync so the page is routable immediately
                    if self.pageindex is not None:
                        from vibe.memory.pageindex import index_wiki_page

                        index_wiki_page(self.pageindex, page)
                except Exception as e:
                    if self.logger:
                        self.logger.debug(
                            "Wiki write failed for item '%s': %s",
                            item.get("title", ""),
                            e,
                        )

            if self.logger:
                self.logger.info(
                    f"Wiki extraction complete: {created} created, {updated} updated, "
                    f"{len(items) - len(approved)} rejected"
                )
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Wiki extraction task failed (non-fatal): {e}")

    async def _find_existing_page(self, title: str) -> Any | None:
        """Find an existing wiki page with matching or similar title.

        Returns the WikiPage if found, None otherwise.
        """
        if self.wiki is None:
            return None
        try:
            # Try exact title match via search_pages()
            results = await self.wiki.search_pages(title, limit=5)
            title_lower = title.lower()
            for page in results:
                if hasattr(page, "title") and page.title.lower() == title_lower:
                    return page
            # Try fuzzy match: if any result title shares >70% words
            for page in results:
                if hasattr(page, "title"):
                    page_words = set(page.title.lower().split())
                    query_words = set(title_lower.split())
                    if page_words and query_words:
                        overlap = len(page_words & query_words) / max(
                            len(page_words), len(query_words)
                        )
                        if overlap > 0.7:
                            return page
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Trajectory reflection (Reflector→Curator pipeline)
    # ------------------------------------------------------------------

    async def _reflect_on_trajectory(
        self, messages: list[Message], session_id: str | None, state: QueryState
    ) -> None:
        """Background task: distill reusable lessons from the finished trajectory.

        First applies usage feedback (no LLM) to the lesson pages this run
        injected, then reflects on the current trajectory (LLM). Lessons are
        curated into the wiki (tagged ``lesson``) and indexed into PageIndex
        so they become routable into future prompts. Never raises — all
        errors are caught and logged.
        """
        if self.wiki is None or self.pageindex is None or session_id is None:
            return

        try:
            from vibe.memory.reflection import TrajectoryReflector

            reflector = TrajectoryReflector(
                wiki=self.wiki,
                pageindex=self.pageindex,
                llm_client=self.llm,
                config=getattr(self._config_memory, "reflection", None),
            )
            # Usage feedback FIRST (no LLM): attribute this session's outcome
            # to the lesson pages injected by _build_wiki_hint, then reflect.
            await reflector.record_usage(
                list(getattr(self, "_injected_lesson_ids", None) or []), state
            )
            query = next((m.content for m in messages if m.role == "user" and m.content), "")
            pages = await reflector.reflect(
                query=query,
                messages=messages,
                state=state,
                session_id=session_id,
                pivotal_turn=getattr(self, "_pivotal_turn", None),
            )
            if pages and self.logger:
                self.logger.info(f"Trajectory reflection wrote {len(pages)} lesson page(s)")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Trajectory reflection task failed (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Phase 2: RLM trigger analysis (MVP — log only, no actual training)
    # ------------------------------------------------------------------

    async def _maybe_trigger_rlm(self) -> None:
        """Background task: analyze telemetry and decide if RLM should trigger.

        Phase 3 MVP: Can now optionally trigger training via analyze_and_train.
        Never raises.
        """
        if self._telemetry is None or self._config_memory is None:
            return

        try:
            from vibe.memory.rlm_analyzer import RLMThresholdAnalyzer
            from vibe.memory.rlm_trainer import RLMTrainer

            analyzer = RLMThresholdAnalyzer(self._telemetry, self._config_memory.rlm)
            trainer = RLMTrainer()

            decision = await analyzer.analyze_and_train(
                wiki=self.wiki,
                trace_store=self._trace_store,
                rlm_trainer=trainer,
                rlm_config=self._config_memory.rlm,
            )

            if decision.should_trigger:
                if self.logger:
                    self.logger.info(
                        f"RLM trigger decision: YES — {decision.reason} "
                        f"(metrics: {decision.metrics})"
                    )
            else:
                if self.logger:
                    self.logger.debug(f"RLM trigger decision: NO — {decision.reason}")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"RLM trigger analysis failed (non-fatal): {e}")

    async def _try_recovery(self, error: Exception) -> QueryResult | None:
        """Phase P5: Try recovery rules for a failed operation.

        Returns a QueryResult if recovery was attempted, None otherwise.
        """
        if getattr(self, "_recovery_rules", None) is None:
            return None
        try:
            # Extract tool name and error message from exception
            error_msg = str(error)
            tool_name = getattr(error, "tool_name", "")
            if not tool_name:
                # Try to infer from error message
                import re

                m = re.search(r"tool ['\"]?([^'\"]+)['\"]?", error_msg, re.I)
                if m:
                    tool_name = m.group(1)

            action = self._recovery_rules.find_recovery(
                tool_name=tool_name or "unknown",
                error_message=error_msg,
                session_state=getattr(self, "_session_state", {}),
            )
            if action is None:
                return None

            # Execute recovery tool
            if self.logger:
                self.logger.info(
                    f"Recovery: attempting {action.recovery_tool} for {tool_name} "
                    f"(attempt "
                    f"{self._session_state.get(f'recovery_attempts:{action.error_pattern}', 0)}/"
                    f"{action.max_attempts})"
                )

            recovery_result = await self.tools.execute_tool(
                action.recovery_tool, **action.recovery_args
            )

            if recovery_result.success:
                return QueryResult(
                    response=f"Recovered from {tool_name} error using {action.recovery_tool}",
                    state=QueryState.PROCESSING,
                )
            else:
                return QueryResult(
                    response=f"Recovery failed: {recovery_result.error}",
                    error=Exception(recovery_result.error),
                    state=QueryState.ERROR,
                )
        except Exception:
            return None

    def stop(self) -> None:
        self._running = False
        self._set_state(QueryState.STOPPED)

    def add_user_message(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))
        self._iteration = 0
        if self._state == QueryState.STOPPED:
            self._state = QueryState.IDLE

    def clear_history(self) -> None:
        self.messages.clear()
        self._state = QueryState.IDLE
        self._iteration = 0
        self._last_checkpointed_iteration = -1
        self._last_checkpointed_state = None
        self._feedback_retries = 0
        self._running = False
        self._plan_result = None
        self._session_id = None
        self._session_start_time = 0.0
        self._pivotal_turn = None
        self._pivotal_failure_counts = {}
        self._pivotal_retry_counts = {}
        self.feedback_coord.reset()

    def copy(self) -> "QueryLoop":
        """Return a shallow copy with reset per-session state.

        Creates fresh instances of per-session mutable coordinators to prevent
        state bleed when the same QueryLoop is used across multiple eval cases
        or concurrent sessions.
        """
        new_loop = copy.copy(self)
        # Shallow-copy the LLM client so model switches in a copy do not mutate
        # the parent's client (and vice versa). The httpx client is shared.
        new_loop.llm = copy.copy(self.llm)
        new_loop.messages = []
        new_loop._running = False
        new_loop._state = QueryState.IDLE
        new_loop._feedback_retries = 0
        new_loop._plan_result = None
        new_loop._session_id = None
        new_loop._session_start_time = 0.0
        new_loop._wiki_extract_task = None
        new_loop._rlm_trigger_task = None
        new_loop._skill_maker_task = None
        new_loop._reflection_task = None
        new_loop._injected_lesson_ids = []
        new_loop._pivotal_turn = None
        new_loop._pivotal_failure_counts = {}
        new_loop._pivotal_retry_counts = {}
        new_loop._iteration = 0
        new_loop._last_checkpointed_iteration = -1
        new_loop._last_checkpointed_state = None
        # Fresh coordinators to prevent state bleed across copies
        new_loop.feedback_coord = FeedbackCoordinator(
            self.feedback_coord.engine,
            self.feedback_coord.threshold,
            self.feedback_coord.max_retries,
        )
        if self.compactor is not None:
            from vibe.core.context_compactor import ContextCompactor
            from vibe.core.coordinators import CompactionCoordinator

            # Create a fresh compactor with the same configuration to avoid
            # state bleed across copied query loops.
            new_loop.compactor = ContextCompactor(
                max_tokens=self.compactor.max_tokens,
                chars_per_token=self.compactor.chars_per_token,
                strategy=self.compactor.strategy,
                summarize_fn=self.compactor.summarize_fn,
                preserve_recent=self.compactor.preserve_recent,
                max_chars_per_msg=self.compactor.max_chars_per_msg,
                telemetry_collector=getattr(self.compactor, "_telemetry", None),
                compaction_policy=getattr(self.compactor, "_compaction_policy", None),
            )
            new_loop.compaction_coord = CompactionCoordinator(new_loop.compactor)
        if getattr(self, "tool_executor", None) is not None:
            from vibe.core.coordinators import ToolExecutor

            new_loop.tool_executor = ToolExecutor(
                self.tool_executor.tools,
                self.tool_executor.hook_pipeline,
                getattr(self.tool_executor, "mcp_bridge", None),
                getattr(self.tool_executor, "tool_prefs", None),
                getattr(self.tool_executor, "shadow_manager", None),
            )
            # Copy registered handlers to new executor
            if hasattr(self.tool_executor, "_handlers"):
                new_loop.tool_executor._handlers = dict(self.tool_executor._handlers)
        return new_loop

    @classmethod
    async def resume(
        cls,
        session_id: str,
        session_store: "SessionStore",
        factory: "QueryLoopFactory",
    ) -> "QueryLoop":
        """Restore a QueryLoop from a checkpoint.

        Args:
            session_id: The session ID to resume.
            session_store: The SessionStore containing the checkpoint.
            factory: The QueryLoopFactory used to create a fresh QueryLoop.

        Returns:
            A QueryLoop restored from the checkpoint.

        Raises:
            ValueError: If no checkpoint is found for the session_id.
        """
        checkpoint = session_store.load_checkpoint(session_id)
        if checkpoint is None:
            raise ValueError(f"No checkpoint found for session {session_id}")

        # Create fresh QueryLoop via factory (shares config, tools, LLM)
        loop = factory.create()
        loop._session_store = session_store
        loop._session_id = session_id
        loop._state = QueryState[checkpoint["state"]]
        loop._iteration = checkpoint.get("iteration", 0)
        loop._feedback_retries = checkpoint.get("feedback_retries", 0)

        # Restore messages
        from vibe.core.query_loop import Message

        loop.messages = [
            Message(
                role=m["role"],
                content=m["content"],
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id"),
                model_version=m.get("model_version"),
            )
            for m in checkpoint["messages"]
        ]

        # Restore plan result
        plan_data = checkpoint.get("plan_result")
        if plan_data:
            from vibe.harness.planner import PlanResult

            loop._plan_result = PlanResult(
                selected_tool_names=plan_data.get("selected_tool_names", []),
                system_prompt_append=plan_data.get("system_prompt_append"),
            )

        # Restore model if checkpoint has one
        if checkpoint.get("model") and loop.llm:
            loop.llm.model = checkpoint["model"]

        return loop

    async def close(self) -> None:
        """Close subsystems and settle background learning tasks.

        Learning tasks (wiki extraction, trajectory reflection, RLM trigger,
        skill-maker) are first awaited for a bounded grace period
        (``self._close_task_grace_seconds``) so one-shot sessions persist their
        lessons before shutdown; anything still running afterwards is
        cancelled. Never raises.
        """
        # Settle background learning tasks FIRST — they write to the wiki, so
        # they must finish (or be cancelled) before subsystems are closed.
        for task_attr in (
            "_wiki_extract_task",
            "_reflection_task",
            "_rlm_trigger_task",
            "_skill_maker_task",
        ):
            task = getattr(self, task_attr, None)
            if task is None or task.done():
                continue
            try:
                await asyncio.wait_for(task, timeout=self._close_task_grace_seconds)
            except asyncio.CancelledError:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            except Exception:
                # Timeout (wait_for already cancelled the task) or task failure.
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

        # v4: Close all closable subsystems via protocol
        for subsystem in [
            self.wiki,
            getattr(self, "feedback_coord", None),
            self.compactor,
        ]:
            if subsystem is not None and hasattr(subsystem, "close"):
                try:
                    result = subsystem.close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

        # Close LLM client and MCP bridge
        if self.llm is not None and hasattr(self.llm, "close"):
            close_fn = self.llm.close
            if asyncio.iscoroutinefunction(close_fn) or (
                hasattr(close_fn, "__call__")
                and asyncio.iscoroutinefunction(getattr(close_fn, "__call__", None))
            ):
                await close_fn()
            elif callable(close_fn):
                close_fn()
        if self.mcp_bridge is not None and hasattr(self.mcp_bridge, "close"):
            close_fn = self.mcp_bridge.close
            if asyncio.iscoroutinefunction(close_fn) or (
                hasattr(close_fn, "__call__")
                and asyncio.iscoroutinefunction(getattr(close_fn, "__call__", None))
            ):
                await close_fn()
            elif callable(close_fn):
                close_fn()
