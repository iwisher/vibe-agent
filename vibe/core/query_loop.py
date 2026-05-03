"""Query loop implementation for Vibe Agent."""

import asyncio
import copy
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, AsyncIterator, Callable

from vibe.core.context_compactor import ContextCompactor
from vibe.core.coordinators import (
    CompactionCoordinator,
    FeedbackCoordinator,
    SecurityCoordinator,
    ToolExecutor,
)
from vibe.core.error_recovery import ErrorRecovery, RetryPolicy
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


@dataclass
class Message:
    role: str
    content: str
    tool_calls: list | None = None
    tool_call_id: str | None = None
    model_version: str | None = None


@dataclass
class QueryResult:
    response: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Exception | None = None
    context_truncated: bool = False
    metrics: Metrics | None = None
    state: QueryState = QueryState.IDLE


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
                max_tokens = int(max_context_tokens) if max_context_tokens is not None else 8000
                context_compactor = ContextCompactor(max_tokens=max_tokens, config=config)

        self.llm = llm_client
        self.tools = tool_system
        self.max_iterations = int(max_iterations) if max_iterations is not None else 50
        self.max_context_tokens = int(max_context_tokens) if max_context_tokens is not None else 8000
        self.compactor = context_compactor or ContextCompactor(max_tokens=self.max_context_tokens)
        self.compaction_coord = CompactionCoordinator(self.compactor)
        self.error_recovery = error_recovery or ErrorRecovery(RetryPolicy())
        self.hook_pipeline = hook_pipeline or HookPipeline()
        self.feedback_coord = FeedbackCoordinator(
            feedback_engine, feedback_threshold, max_feedback_retries
        )
        self.tool_executor = ToolExecutor(
            tool_system, self.hook_pipeline, mcp_bridge=mcp_bridge
        )
        # Phase 6: 5-layer security defense
        sec_cfg = security_config
        if sec_cfg is None and config is not None and hasattr(config, "security"):
            sec_cfg = config.security
        self.security_coord = None
        if sec_cfg is not None:
            self.security_coord = SecurityCoordinator(
                config=sec_cfg,
                llm_client=llm_client,
                checkpoint_manager=checkpoint_manager,
            )
        self.messages: list[Message] = []
        self._running = False
        self._state = QueryState.IDLE
        self._feedback_retries = 0
        self.instruction_set = instruction_set
        self.mcp_bridge = mcp_bridge
        self.logger = logger
        self.context_planner = context_planner or ContextPlanner(trace_store=trace_store)
        self._plan_result: PlanResult | None = None
        self._trace_store = trace_store
        self._session_id: str | None = None
        # v4: Tripartite Memory System
        self.wiki = wiki
        self.pageindex = pageindex
        self._telemetry = telemetry
        self._wiki_extract_task: asyncio.Task | None = None  # Phase 1b: async extraction
        self._rlm_trigger_task: asyncio.Task | None = None  # Phase 2: RLM trigger
        self._session_start_time: float = 0.0
        self._config_memory = getattr(config, "tripartite", None) if config else None
        # Phase 3.2: Session checkpointing for durable suspension/resumption
        self._session_store = session_store
        self._iteration = 0

    @property
    def state(self) -> QueryState:
        return self._state

    def _set_state(self, state: QueryState) -> None:
        self._state = state
        self._checkpoint()

    def _checkpoint(self) -> None:
        """Serialize current state to SessionStore. Called on every state transition."""
        if self._session_store is None or self._session_id is None:
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

    async def run(self, initial_query: str | None = None) -> AsyncIterator[QueryResult]:
        if self._state == QueryState.STOPPED:
            return
        self._running = True
        self._set_state(QueryState.PLANNING)
        import uuid
        self._session_id = str(uuid.uuid4())
        self._session_start_time = time.time()
        if self.logger:
            self.logger.info(f"Starting QueryLoop run. Initial query: {initial_query}")
        try:
            if initial_query:
                self.messages.append(Message(role="user", content=initial_query))

            # v4: Wiki retrieval happens BEFORE planner (async context)
            wiki_hint = ""
            if initial_query and self.wiki is not None and self.pageindex is not None:
                try:
                    routing_timeout = 2.0
                    if hasattr(self, '_config_memory'):
                        routing_timeout = getattr(
                            self._config_memory, 'routing_timeout_seconds', 2.0
                        )
                    wiki_nodes = await asyncio.wait_for(
                        self.pageindex.route(initial_query),
                        timeout=routing_timeout,
                    )
                    if wiki_nodes:
                        wiki_hint = "\n\n## Relevant Knowledge\n" + "\n".join(
                            f"- [[{n.node_id}]] {n.title}: {n.description}"
                            for n in wiki_nodes[:3]
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
                    available_tools=self.tools.get_tool_schemas() + (self.mcp_bridge.get_tool_schemas() if self.mcp_bridge else []),
                    available_skills=self.instruction_set.skills if self.instruction_set else [],
                    available_mcps=[
                        {"name": cfg.name, "description": cfg.description}
                        for cfg in (self.mcp_bridge.configs if self.mcp_bridge else [])
                    ],
                    wiki_hint=wiki_hint,  # v4: pass wiki hints via PlanRequest
                )
                self._plan_result = self.context_planner.plan(plan_request)
                if self.logger:
                    self.logger.info(f"Planner selected tools: {self._plan_result.selected_tool_names}")
                if self._plan_result.system_prompt_append:
                    self.messages.insert(
                        0,
                        Message(role="system", content=self._plan_result.system_prompt_append),
                    )

            iteration = self._iteration
            max_iterations = int(self.max_iterations) if self.max_iterations is not None else 50
            while self._running and iteration < max_iterations:
                iteration += 1
                self._iteration = iteration
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
                        if self.logger:
                            from vibe.tools._utils import extract_tool_call_name
                            tool_names = [extract_tool_call_name(tc) for tc in response.tool_calls]
                            self.logger.info(f"LLM requested tools: {tool_names}")
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
                if iteration >= max_iterations:
                    self._set_state(QueryState.INCOMPLETE)
                else:
                    self._set_state(QueryState.COMPLETED)
        finally:
            self._running = False
            # Record session telemetry
            if self._telemetry is not None and self._session_id:
                try:
                    elapsed = time.time() - self._session_start_time
                    total_chars = sum(
                        len(m.content) for m in self.messages if m.content
                    )
                    self._telemetry.record_session(
                        session_id=self._session_id,
                        duration_seconds=elapsed,
                        total_chars=total_chars,
                        state=self._state.name,
                    )
                except Exception:
                    pass

            # Phase 1b: Spawn background wiki extraction (non-blocking)
            if (
                self.wiki is not None
                and self._config_memory is not None
                and getattr(self._config_memory.wiki, "auto_extract", False)
                and self._state in (QueryState.COMPLETED, QueryState.INCOMPLETE)
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

            # Phase 2: Spawn background RLM trigger analysis (non-blocking, MVP: log only)
            if (
                self._telemetry is not None
                and self._config_memory is not None
                and getattr(self._config_memory.rlm, "enabled", False)
            ):
                try:
                    self._rlm_trigger_task = asyncio.create_task(
                        self._maybe_trigger_rlm()
                    )
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"RLM trigger task spawn failed (non-fatal): {e}")

            # Log session to trace store if available
            if self._trace_store and self._session_id:
                try:
                    tool_results = []
                    for msg in self.messages:
                        if msg.role == "tool":
                            tool_results.append({
                                "tool_call_id": msg.tool_call_id,
                                "content": msg.content,
                            })
                    self._trace_store.log_session(
                        session_id=self._session_id,
                        messages=[
                            {"role": m.role, "content": m.content}
                            for m in self.messages
                        ],
                        tool_results=tool_results,
                        success=self._state == QueryState.COMPLETED,
                        model=self.llm.model if self.llm else "unknown",
                        error=str(self._state.name) if self._state in (QueryState.ERROR, QueryState.INCOMPLETE) else None,
                    )
                except Exception:
                    # Logging failures must not crash the session
                    pass

            # Phase 3.2: Delete checkpoint on completion (session is now durable in trace_store)
            if self._session_store and self._session_id:
                try:
                    self._session_store.delete_checkpoint(self._session_id)
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

    async def _execute_with_security(self, tool_calls: list) -> list[ToolResult]:
        """Execute tool calls with 5-layer security checks.

        Returns results in the same order as tool_calls, with blocked calls
        replaced by error ToolResults.
        """
        if self.security_coord is None:
            return await self.tool_executor.execute(tool_calls)

        results: list[ToolResult | None] = [None] * len(tool_calls)
        allowed_calls: list[Any] = []
        allowed_indices: list[int] = []

        for i, call in enumerate(tool_calls):
            call_name = extract_tool_call_name(call)
            arguments = extract_tool_call_arguments(call)
            check = self.security_coord.evaluate_tool_call(call_name, arguments)
            if check.allowed:
                allowed_calls.append(call)
                allowed_indices.append(i)
            else:
                results[i] = ToolResult(
                    success=False,
                    content=None,
                    error=f"Security blocked: {check.reason}",
                )

        if allowed_calls:
            executed = await self.tool_executor.execute(allowed_calls)
            for idx, result in zip(allowed_indices, executed):
                results[idx] = result

        return [r for r in results if r is not None]

    async def _process_tool_response(self, response: LLMResponse, metrics: Metrics) -> QueryResult:
        """Handle a response containing tool calls."""
        self._set_state(QueryState.TOOL_EXECUTION)
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

    async def _process_content_response(self, response: LLMResponse, metrics: Metrics) -> tuple[bool, QueryResult | None]:
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
                metrics=metrics,
                state=QueryState.PROCESSING,
            )
        self._set_state(QueryState.COMPLETED)
        return False, QueryResult(response=response.content or "", metrics=metrics, state=self._state)

    async def _execute_tool_calls(self, tool_calls: list) -> list[ToolResult]:
        """Deprecated: delegates to ToolExecutor."""
        return await self.tool_executor.execute(tool_calls)

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

    # ------------------------------------------------------------------
    # Phase 1b: Wiki auto-extraction
    # ------------------------------------------------------------------

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
            )

            items = await extractor.extract_from_session(messages, session_id)
            if not items:
                return

            # Apply quality gates
            novelty_threshold = 0.5
            confidence_threshold = 0.8
            if self._config_memory is not None:
                novelty_threshold = getattr(
                    self._config_memory.wiki, "novelty_threshold", 0.5
                )
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
                        await self.wiki.update_page(
                            page_id=existing.id,
                            content=item.get("content", ""),
                            citations=new_citations,
                        )
                        updated += 1
                    else:
                        await self.wiki.create_page(
                            title=item.get("title", ""),
                            content=item.get("content", ""),
                            tags=item.get("tags", []),
                            citations=item.get("citations", []),
                            status="draft",
                        )
                        created += 1
                except Exception as e:
                    logger.debug("Wiki write failed for item '%s': %s", item.get("title", ""), e)

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
                        overlap = len(page_words & query_words) / max(len(page_words), len(query_words))
                        if overlap > 0.7:
                            return page
            return None
        except Exception:
            return None

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
                rlm_config=self._config_memory.rlm
            )

            if decision.should_trigger:
                if self.logger:
                    self.logger.info(
                        f"RLM trigger decision: YES — {decision.reason} (metrics: {decision.metrics})"
                    )
            else:
                if self.logger:
                    self.logger.debug(f"RLM trigger decision: NO — {decision.reason}")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"RLM trigger analysis failed (non-fatal): {e}")

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
        self.feedback_coord.reset()

    def copy(self) -> "QueryLoop":
        """Return a shallow copy with reset per-session state.

        Creates fresh instances of per-session mutable coordinators to prevent
        state bleed when the same QueryLoop is used across multiple eval cases
        or concurrent sessions.
        """
        new_loop = copy.copy(self)
        new_loop.messages = []
        new_loop._running = False
        new_loop._state = QueryState.IDLE
        new_loop._feedback_retries = 0
        new_loop._plan_result = None
        new_loop._session_id = None
        new_loop._session_start_time = 0.0
        new_loop._wiki_extract_task = None
        new_loop._rlm_trigger_task = None
        new_loop._iteration = 0
        # Fresh coordinators to prevent state bleed across copies
        new_loop.feedback_coord = FeedbackCoordinator(
            self.feedback_coord.engine,
            self.feedback_coord.threshold,
            self.feedback_coord.max_retries,
        )
        if self.compactor is not None:
            from vibe.core.coordinators import CompactionCoordinator
            new_loop.compactor = CompactionCoordinator(self.compactor.compactor)
        if getattr(self, "tool_executor", None) is not None:
            from vibe.core.coordinators import ToolExecutor
            new_loop.tool_executor = ToolExecutor(
                self.tool_executor.tools,
                self.tool_executor.hook_pipeline,
                getattr(self.tool_executor, "mcp_bridge", None),
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
        """Close all subsystems via Closable protocol. Cancel pending background tasks."""
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

        # Cancel any pending background tasks (Phase 1b + Phase 2)
        for task_attr in ("_wiki_extract_task", "_rlm_trigger_task"):
            task = getattr(self, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close LLM client and MCP bridge
        if self.llm is not None and hasattr(self.llm, 'close'):
            close_fn = self.llm.close
            if asyncio.iscoroutinefunction(close_fn) or (hasattr(close_fn, '__call__') and asyncio.iscoroutinefunction(getattr(close_fn, '__call__', None))):
                await close_fn()
            elif callable(close_fn):
                close_fn()
        if self.mcp_bridge is not None and hasattr(self.mcp_bridge, 'close'):
            close_fn = self.mcp_bridge.close
            if asyncio.iscoroutinefunction(close_fn) or (hasattr(close_fn, '__call__') and asyncio.iscoroutinefunction(getattr(close_fn, '__call__', None))):
                await close_fn()
            elif callable(close_fn):
                close_fn()
