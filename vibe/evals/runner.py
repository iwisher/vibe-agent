"""Eval runner that executes eval cases against a QueryLoop and checks expectations."""

import asyncio
import time
from pathlib import Path
from typing import Any

from vibe.core.query_loop import QueryLoop, QueryResult
from vibe.harness.memory.eval_store import EvalCase, EvalResult, EvalStore
from vibe.evals.observability import Observability
from vibe.tools._utils import extract_tool_call_name


class EvalRunner:
    """Runs EvalCases through a QueryLoop and validates expected outcomes."""

    def __init__(
        self,
        query_loop: QueryLoop,
        eval_store: EvalStore | None = None,
        observability: Observability | None = None,
        max_concurrency: int = 3,
        run_holdout: bool = False,
    ):
        self.query_loop = query_loop
        self.eval_store = eval_store
        self.obs = observability
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.run_holdout = run_holdout

    async def run_case(self, case: EvalCase) -> EvalResult:
        start_time = time.time()
        case_span = None
        if self.obs:
            case_span = self.obs.start_span(
                "eval_case",
                attributes={"case_id": case.id, "tags": case.tags},
            )

        self.query_loop.clear_history()
        results: list[QueryResult] = []

        # Span for LLM interaction
        llm_span = None
        if self.obs:
            llm_span = self.obs.start_span("llm_call", parent=case_span)

        try:
            async with asyncio.timeout(case.timeout_seconds):
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
        except asyncio.TimeoutError:
            if llm_span:
                self.obs.finish_span(llm_span)
            if case_span:
                case_span.finish(status="error", error_message="Timeout")
                self.obs.finish_span(case_span)
            return EvalResult(
                eval_id=case.id,
                passed=False,
                diff={"reason": f"Exceeded timeout of {case.timeout_seconds}s"},
                total_tokens=0,
            )
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
        diff: dict[str, Any] = {}
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
            ok, msg = _assertion_span("file_exists", lambda: self._check_file_exists(expected))
            if not ok:
                passed = False
                diff["file_exists"] = msg

        # file_contains + contains_text
        if "file_contains" in expected and "contains_text" in expected:
            ok, msg = _assertion_span("file_contains", lambda: self._check_file_contains(expected))
            if not ok:
                passed = False
                diff["contains_text"] = msg

        # stdout_contains
        if "stdout_contains" in expected:
            ok, msg = _assertion_span("stdout_contains", lambda: self._check_stdout_contains(expected, results))
            if not ok:
                passed = False
                diff["stdout_contains"] = msg

        # tool_called
        if "tool_called" in expected:
            ok, msg = _assertion_span("tool_called", lambda: self._check_tool_called(expected))
            if not ok:
                passed = False
                diff["tool_called"] = msg

        # tool_sequence
        if "tool_sequence" in expected:
            ok, msg = _assertion_span("tool_sequence", lambda: self._check_tool_sequence(expected))
            if not ok:
                passed = False
                diff["tool_sequence"] = msg

        # no_tool_called
        if "no_tool_called" in expected:
            ok, msg = _assertion_span("no_tool_called", lambda: self._check_no_tool_called(results))
            if not ok:
                passed = False
                diff["no_tool_called"] = msg

        # context_truncated
        if "context_truncated" in expected:
            ok, msg = _assertion_span("context_truncated", lambda: self._check_context_truncated(expected, results))
            if not ok:
                passed = False
                diff["context_truncated"] = msg

        # response_contains
        if "response_contains" in expected:
            ok, msg = _assertion_span("response_contains", lambda: self._check_response_contains(expected, results))
            if not ok:
                passed = False
                diff.update(msg)

        # response_contains_any
        if "response_contains_any" in expected:
            ok, msg = _assertion_span("response_contains_any", lambda: self._check_response_contains_any(expected, results))
            if not ok:
                passed = False
                diff["response_contains_any"] = msg

        # min_response_length
        if "min_response_length" in expected:
            ok, msg = _assertion_span("min_response_length", lambda: self._check_min_response_length(expected, results))
            if not ok:
                passed = False
                diff["min_response_length"] = msg

        # metrics_threshold (latency / token budget)
        latency = time.time() - start_time
        if "metrics_threshold" in expected:
            ok, msg = _assertion_span("metrics_threshold", lambda: self._check_metrics_threshold(expected, latency, total_tokens))
            if not ok:
                passed = False
                diff["metrics_threshold"] = msg

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

    # ─── Named assertion check methods (return (bool, str|dict)) ───

    def _check_file_exists(self, expected: dict[str, Any]) -> tuple[bool, str]:
        path = Path(expected["file_exists"]).expanduser()
        if not path.exists():
            return False, f"Missing {path}"
        return True, ""

    def _check_file_contains(self, expected: dict[str, Any]) -> tuple[bool, str]:
        path = Path(expected["file_contains"]).expanduser()
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return False, str(e)
        if expected["contains_text"] not in content:
            return False, f"Expected '{expected['contains_text']}' not found in {path}"
        return True, ""

    def _check_stdout_contains(self, expected: dict[str, Any], results: list[QueryResult]) -> tuple[bool, str]:
        target = expected["stdout_contains"]
        for r in results:
            for tr in r.tool_results:
                text = str(tr.content) if tr.content is not None else (tr.error or "")
                if target in text:
                    return True, ""
        return False, f"Expected '{target}' not found in tool outputs"

    def _check_tool_called(self, expected: dict[str, Any]) -> tuple[bool, str]:
        target_tool = expected["tool_called"]
        for m in self.query_loop.messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in (m.tool_calls or []):
                    if extract_tool_call_name(tc) == target_tool:
                        return True, ""
        return False, f"Expected tool '{target_tool}' was not called"

    def _check_tool_sequence(self, expected: dict[str, Any]) -> tuple[bool, str]:
        expected_seq = expected["tool_sequence"]
        if isinstance(expected_seq, str):
            expected_seq = [expected_seq]
        actual_seq = []
        for m in self.query_loop.messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in (m.tool_calls or []):
                    tc_name = extract_tool_call_name(tc)
                    if tc_name:
                        actual_seq.append(tc_name)
        if actual_seq != expected_seq:
            return False, f"Expected sequence {expected_seq}, got {actual_seq}"
        return True, ""

    def _check_no_tool_called(self, results: list[QueryResult]) -> tuple[bool, str]:
        any_tool = any(r.tool_results for r in results)
        if any_tool:
            return False, "Expected no tool calls, but tools were invoked"
        return True, ""

    def _check_context_truncated(self, expected: dict[str, Any], results: list[QueryResult]) -> tuple[bool, str]:
        truncated = any(r.context_truncated for r in results)
        if expected["context_truncated"] and not truncated:
            return False, "Expected context truncation, but it did not occur"
        if not expected["context_truncated"] and truncated:
            return False, "Context was truncated unexpectedly"
        return True, ""

    def _check_response_contains(self, expected: dict[str, Any], results: list[QueryResult]) -> tuple[bool, dict[str, str]]:
        targets = expected["response_contains"]
        if isinstance(targets, str):
            targets = [targets]
        failures = {}
        for target in targets:
            found = any(target in (r.response or "") for r in results)
            if not found:
                key = f"response_contains_{target[:20]}"
                failures[key] = f"Expected '{target}' not found in responses"
        if failures:
            return False, failures
        return True, {}

    def _check_response_contains_any(self, expected: dict[str, Any], results: list[QueryResult]) -> tuple[bool, str]:
        targets = expected["response_contains_any"]
        if isinstance(targets, str):
            targets = [targets]
        responses = [r.response or "" for r in results]
        combined = "\n".join(responses)
        found_any = any(target.lower() in combined.lower() for target in targets)
        if not found_any:
            return False, f"Expected at least one of {targets} not found in responses"
        return True, ""

    def _check_min_response_length(self, expected: dict[str, Any], results: list[QueryResult]) -> tuple[bool, str]:
        min_len = expected["min_response_length"]
        responses = [r.response or "" for r in results]
        total_len = sum(len(r) for r in responses)
        if total_len < min_len:
            return False, f"Total response length {total_len} < required {min_len}"
        return True, ""

    def _check_metrics_threshold(self, expected: dict[str, Any], latency: float, total_tokens: int) -> tuple[bool, str]:
        thresholds = expected["metrics_threshold"]
        if isinstance(thresholds, dict):
            max_latency = thresholds.get("max_latency_seconds")
            max_tokens = thresholds.get("max_total_tokens")
            if max_latency is not None and latency > max_latency:
                return False, f"Latency {latency:.2f}s > threshold {max_latency}s"
            if max_tokens is not None and total_tokens > max_tokens:
                return False, f"Total tokens {total_tokens} > threshold {max_tokens}"
        return True, ""

    async def run_all(self, cases: list[EvalCase]) -> list[EvalResult]:
        filtered = [c for c in cases if self.run_holdout or not c.holdout_set]
        async def _run(case: EvalCase) -> EvalResult:
            async with self._semaphore:
                return await self.run_case(case)

        try:
            return await asyncio.gather(*(_run(c) for c in filtered))
        finally:
            await self.query_loop.close()
