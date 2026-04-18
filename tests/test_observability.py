"""Tests for observability integration with EvalRunner."""

import asyncio
from pathlib import Path

import pytest

from vibe.evals.observability import Observability, MetricType
from vibe.harness.memory.eval_store import EvalCase


class FakeQueryLoop:
    """Minimal fake QueryLoop for testing."""

    def __init__(self, responses=None):
        self.messages = []
        self._responses = responses or []

    def clear_history(self):
        self.messages = []

    async def run(self, initial_query=""):
        class FakeResult:
            def __init__(self, response, tool_results, error=None, metrics=None):
                self.response = response
                self.tool_results = tool_results
                self.error = error
                self.metrics = metrics
                self.context_truncated = False

        class FakeMetrics:
            def __init__(self, total=0):
                self.total_tokens = total

        for resp in self._responses:
            yield FakeResult(
                response=resp.get("response", ""),
                tool_results=resp.get("tool_results", []),
                error=resp.get("error"),
                metrics=FakeMetrics(resp.get("total_tokens", 0)),
            )

    class FakeLLM:
        async def close(self):
            pass

    llm = FakeLLM()


class FakeToolResult:
    def __init__(self, name="bash", content="", error=None):
        self.tool_name = name
        self.content = content
        self.error = error


def test_observability_spans_created():
    """Test that EvalRunner creates spans when observability is provided."""
    from vibe.evals.runner import EvalRunner

    obs = Observability()
    ql = FakeQueryLoop(responses=[{"response": "hello", "total_tokens": 10}])
    runner = EvalRunner(query_loop=ql, observability=obs)

    case = EvalCase(
        id="test-span-001",
        tags=["test"],
        input={"prompt": "hi"},
        expected={"response_contains": "hello"},
    )

    result = asyncio.run(runner.run_case(case))

    # Should have created spans
    span_names = [s.name for s in obs._spans]
    assert "eval_case" in span_names
    assert "llm_call" in span_names
    assert "assertion_check" in span_names

    # eval_case should be the parent
    eval_spans = [s for s in obs._spans if s.name == "eval_case"]
    assert len(eval_spans) == 1
    assert eval_spans[0].attributes.get("case_id") == "test-span-001"


def test_observability_metrics_recorded():
    """Test that EvalRunner records metrics."""
    from vibe.evals.runner import EvalRunner

    obs = Observability()
    ql = FakeQueryLoop(responses=[{"response": "42", "total_tokens": 25}])
    runner = EvalRunner(query_loop=ql, observability=obs)

    case = EvalCase(
        id="test-metric-001",
        tags=["test"],
        input={"prompt": "what is 6*7"},
        expected={"response_contains": "42"},
    )

    result = asyncio.run(runner.run_case(case))

    # Check metrics
    assert result.passed
    token_keys = [k for k in obs._gauges.keys() if "llm_token_usage" in k]
    assert len(token_keys) >= 1
    assert obs._gauges[token_keys[0]] == 25

    passed_keys = [k for k in obs._counters.keys() if "eval_passed" in k]
    assert len(passed_keys) >= 1

    latency_keys = [k for k in obs._histograms.keys() if "eval_latency" in k]
    assert len(latency_keys) >= 1


def test_observability_no_double_counting():
    """Test that metrics are not duplicated between runner and caller."""
    from vibe.evals.runner import EvalRunner

    obs = Observability()
    ql = FakeQueryLoop(responses=[{"response": "ok", "total_tokens": 5}])
    runner = EvalRunner(query_loop=ql, observability=obs)

    case = EvalCase(
        id="test-no-dup-001",
        tags=["test"],
        input={"prompt": "test"},
        expected={"response_contains": "ok"},
    )

    asyncio.run(runner.run_case(case))

    # Should only have one eval_case span per run_case call
    eval_spans = [s for s in obs._spans if s.name == "eval_case"]
    assert len(eval_spans) == 1


def test_observability_export():
    """Test that observability data can be exported."""
    obs = Observability()

    # Record some data
    obs.counter("test_counter", 1.0, labels={"case": "a"})
    obs.counter("test_counter", 1.0, labels={"case": "a"})
    obs.gauge("test_gauge", 42.0)
    obs.histogram("test_hist", 1.0)
    obs.histogram("test_hist", 2.0)
    obs.histogram("test_hist", 3.0)

    with obs.span("parent_span", attributes={"key": "val"}):
        with obs.span("child_span"):
            pass

    # Export
    metrics_path = obs.export_metrics()
    trace_path = obs.export_trace()

    assert Path(metrics_path).exists()
    assert Path(trace_path).exists()

    # Verify metrics content
    import json
    with open(metrics_path) as f:
        metrics_data = json.load(f)
    assert metrics_data["counters"]["test_counter{case=a}"] == 2.0
    assert metrics_data["gauges"]["test_gauge"] == 42.0
    assert metrics_data["histograms"]["test_hist"]["count"] == 3
    assert metrics_data["histograms"]["test_hist"]["p50"] == 2.0

    # Verify trace content
    with open(trace_path) as f:
        trace_data = json.load(f)
    assert trace_data["span_count"] == 2
    assert trace_data["trace_count"] == 1

    span_names = [s["name"] for s in trace_data["spans"]]
    assert "parent_span" in span_names
    assert "child_span" in span_names

    # Verify parent-child relationship
    parent = [s for s in trace_data["spans"] if s["name"] == "parent_span"][0]
    child = [s for s in trace_data["spans"] if s["name"] == "child_span"][0]
    assert child["parent_id"] == parent["span_id"]


def test_observability_summary():
    """Test summary method."""
    obs = Observability()
    obs.counter("c1", 1)
    obs.gauge("g1", 10)
    obs.histogram("h1", 5)

    summary = obs.summary()
    assert summary["metrics_count"] == 3
    assert summary["span_count"] == 0
    assert "c1" in summary["counters"]
    assert "g1" in summary["gauges"]
    assert "h1" in summary["histogram_keys"]


def test_observability_percentile():
    """Test percentile calculation."""
    obs = Observability()
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    assert obs._percentile(values, 0.0) == 1.0
    assert obs._percentile(values, 0.5) == 5.5
    assert obs._percentile(values, 1.0) == 10.0
    assert obs._percentile([], 0.5) == 0.0
