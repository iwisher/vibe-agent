"""Eval runner that executes eval cases against a QueryLoop and checks expectations."""

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from vibe.core.query_loop import QueryLoop, QueryResult
from vibe.harness.memory.eval_store import EvalCase, EvalResult, EvalStore
from vibe.evals.observability import Observability


class EvalRunner:
    """Runs EvalCases through a QueryLoop and validates expected outcomes."""

    def __init__(
        self,
        query_loop: QueryLoop,
        eval_store: Optional[EvalStore] = None,
        observability: Optional[Observability] = None,
        max_concurrency: int = 3,
    ):
        self.query_loop = query_loop
        self.eval_store = eval_store
        self.obs = observability
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_case(self, case: EvalCase) -> EvalResult:
        start_time = time.time()
        case_span = None
        if self.obs:
            case_span = self.obs.start_span(
                "eval_case",
                attributes={"case_id": case.id, "tags": case.tags},
            )

        self.query_loop.clear_history()
        results: List[QueryResult] = []

        # Span for LLM interaction
        llm_span = None
        if self.obs:
            llm_span = self.obs.start_span("llm_call", parent=case_span)

        try:
            async for result in self.query_loop.run(
                initial_query=case.input.get("prompt", "")
            ):
                results.append(result)
                # Record tool execution spans
                if result.tool_results and self.obs:
                    for tr in result.tool_results:
                        with self.obs.span(
                            "tool_execution",
                            attributes={"tool": getattr(tr, "tool_name", "unknown")},
                        ):
                            pass
        finally:
            if llm_span:
                self.obs.finish_span(llm_span)

        if not results:
            if case_span:
                case_span.finish(status="error", error_message="No results produced")
                self.obs.finish_span(case_span)
            return EvalResult(
                eval_id=case.id,
                passed=False,
                diff={"reason": "No results produced"},
                total_tokens=0,
            )

        final = results[-1]
        diff: Dict[str, Any] = {}
        passed = True
        expected = case.expected or {}

        # Report any errors from the query loop
        errors = [r.error for r in results if r.error]
        if errors:
            diff["_errors"] = "; ".join(str(e)[:200] for e in errors)

        # Accumulate token usage across all turns
        total_tokens = 0
        for r in results:
            if r.metrics:
                total_tokens += getattr(r.metrics, "total_tokens", 0)

        # Record token usage metric
        if self.obs:
            self.obs.gauge("llm_token_usage", total_tokens, labels={"case_id": case.id})

        # ─── Assertion checks with spans ───
        def _assertion_span(name: str, check_fn):
            if self.obs:
                with self.obs.span("assertion_check", attributes={"assertion": name}):
                    return check_fn()
            return check_fn()

        # file_exists
        if "file_exists" in expected:

            def check_file_exists():
                nonlocal passed
                path = Path(expected["file_exists"]).expanduser()
                if not path.exists():
                    passed = False
                    diff["file_exists"] = f"Missing {path}"

            _assertion_span("file_exists", check_file_exists)

        # file_contains + contains_text
        if "file_contains" in expected and "contains_text" in expected:

            def check_file_contains():
                nonlocal passed
                path = Path(expected["file_contains"]).expanduser()
                try:
                    content = path.read_text(encoding="utf-8")
                except Exception as e:
                    passed = False
                    diff["file_contains"] = str(e)
                    content = ""
                if expected["contains_text"] not in content:
                    passed = False
                    diff["contains_text"] = (
                        f"Expected '{expected['contains_text']}' not found in {path}"
                    )

            _assertion_span("file_contains", check_file_contains)

        # stdout_contains
        if "stdout_contains" in expected:

            def check_stdout():
                nonlocal passed
                target = expected["stdout_contains"]
                found = False
                for r in results:
                    for tr in r.tool_results:
                        text = str(tr.content) if tr.content is not None else (tr.error or "")
                        if target in text:
                            found = True
                            break
                    if found:
                        break
                if not found:
                    passed = False
                    diff["stdout_contains"] = f"Expected '{target}' not found in tool outputs"

            _assertion_span("stdout_contains", check_stdout)

        # tool_called
        if "tool_called" in expected:

            def check_tool_called():
                nonlocal passed
                target_tool = expected["tool_called"]
                found = False
                for m in self.query_loop.messages:
                    if m.role == "assistant" and m.tool_calls:
                        for tc in (m.tool_calls or []):
                            tc_name = (
                                (tc.get("name") or tc.get("function", {}).get("name"))
                                if isinstance(tc, dict)
                                else getattr(tc, "name", "")
                            )
                            if tc_name == target_tool:
                                found = True
                                break
                        if found:
                            break
                if not found:
                    passed = False
                    diff["tool_called"] = f"Expected tool '{target_tool}' was not called"

            _assertion_span("tool_called", check_tool_called)

        # tool_sequence
        if "tool_sequence" in expected:

            def check_tool_sequence():
                nonlocal passed
                expected_seq = expected["tool_sequence"]
                if isinstance(expected_seq, str):
                    expected_seq = [expected_seq]
                actual_seq = []
                for m in self.query_loop.messages:
                    if m.role == "assistant" and m.tool_calls:
                        for tc in (m.tool_calls or []):
                            tc_name = (
                                (tc.get("name") or tc.get("function", {}).get("name"))
                                if isinstance(tc, dict)
                                else getattr(tc, "name", "")
                            )
                            if tc_name:
                                actual_seq.append(tc_name)
                if actual_seq != expected_seq:
                    passed = False
                    diff["tool_sequence"] = (
                        f"Expected sequence {expected_seq}, got {actual_seq}"
                    )

            _assertion_span("tool_sequence", check_tool_sequence)

        # no_tool_called
        if "no_tool_called" in expected:

            def check_no_tool():
                nonlocal passed
                any_tool = any(r.tool_results for r in results)
                if any_tool:
                    passed = False
                    diff["no_tool_called"] = "Expected no tool calls, but tools were invoked"

            _assertion_span("no_tool_called", check_no_tool)

        # context_truncated
        if "context_truncated" in expected:

            def check_truncated():
                nonlocal passed
                truncated = any(r.context_truncated for r in results)
                if expected["context_truncated"] and not truncated:
                    passed = False
                    diff["context_truncated"] = "Expected context truncation, but it did not occur"
                elif not expected["context_truncated"] and truncated:
                    passed = False
                    diff["context_truncated"] = "Context was truncated unexpectedly"

            _assertion_span("context_truncated", check_truncated)

        # response_contains
        if "response_contains" in expected:

            def check_response():
                nonlocal passed
                targets = expected["response_contains"]
                if isinstance(targets, str):
                    targets = [targets]
                for target in targets:
                    found = any(target in (r.response or "") for r in results)
                    if not found:
                        passed = False
                        key = f"response_contains_{target[:20]}"
                        diff[key] = f"Expected '{target}' not found in responses"

            _assertion_span("response_contains", check_response)

        # response_contains_any
        if "response_contains_any" in expected:

            def check_response_any():
                nonlocal passed
                targets = expected["response_contains_any"]
                if isinstance(targets, str):
                    targets = [targets]
                responses = [r.response or "" for r in results]
                combined = "\n".join(responses)
                found_any = any(target.lower() in combined.lower() for target in targets)
                if not found_any:
                    passed = False
                    diff["response_contains_any"] = (
                        f"Expected at least one of {targets} not found in responses"
                    )

            _assertion_span("response_contains_any", check_response_any)

        # min_response_length
        if "min_response_length" in expected:

            def check_min_length():
                nonlocal passed
                min_len = expected["min_response_length"]
                responses = [r.response or "" for r in results]
                total_len = sum(len(r) for r in responses)
                if total_len < min_len:
                    passed = False
                    diff["min_response_length"] = (
                        f"Total response length {total_len} < required {min_len}"
                    )

            _assertion_span("min_response_length", check_min_length)

        # metrics_threshold (latency / token budget)
        latency = time.time() - start_time
        if "metrics_threshold" in expected:

            def check_metrics_threshold():
                nonlocal passed
                thresholds = expected["metrics_threshold"]
                if isinstance(thresholds, dict):
                    max_latency = thresholds.get("max_latency_seconds")
                    max_tokens = thresholds.get("max_total_tokens")
                    if max_latency is not None and latency > max_latency:
                        passed = False
                        diff["metrics_threshold"] = (
                            f"Latency {latency:.2f}s > threshold {max_latency}s"
                        )
                    if max_tokens is not None and total_tokens > max_tokens:
                        passed = False
                        diff["metrics_threshold"] = (
                            f"Total tokens {total_tokens} > threshold {max_tokens}"
                        )

            _assertion_span("metrics_threshold", check_metrics_threshold)

        # Record metrics
        if self.obs:
            self.obs.histogram("eval_latency", latency, labels={"case_id": case.id})
            self.obs.counter("eval_passed", 1.0 if passed else 0.0, labels={"case_id": case.id})
            if case_span:
                case_span.finish(
                    status="ok" if passed else "error",
                    error_message=str(diff) if not passed else None,
                )
                self.obs.finish_span(case_span)

        result = EvalResult(
            eval_id=case.id, passed=passed, diff=diff, total_tokens=total_tokens,
            latency_seconds=latency,
        )
        if self.eval_store:
            self.eval_store.record_result(result)
        return result

    async def run_all(self, cases: List[EvalCase]) -> List[EvalResult]:
        async def _run(case: EvalCase) -> EvalResult:
            async with self._semaphore:
                return await self.run_case(case)

        try:
            return await asyncio.gather(*(_run(c) for c in cases))
        finally:
            await self.query_loop.close()
