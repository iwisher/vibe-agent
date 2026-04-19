"""Observability for vibe-agent evals: structured logging, metrics, and tracing.

Provides OpenTelemetry-style spans and counters for debugging eval runs,
diagnosing bottlenecks, and exporting to external monitoring systems.
"""

import json
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass
class Metric:
    name: str
    type: MetricType
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class Span:
    """A trace span representing a unit of work."""

    name: str
    trace_id: str
    span_id: str
    parent_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # ok, error
    error_message: Optional[str] = None

    def duration_ms(self) -> float:
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def finish(self, status: str = "ok", error_message: Optional[str] = None):
        self.end_time = time.time()
        self.status = status
        if error_message:
            self.error_message = error_message


class Observability:
    """Central observability collector for eval runs.
    
    NOT a singleton — create separate instances for parallel runs.
    Use get_default() for the global default instance.
    """

    _default_instance: Optional["Observability"] = None

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or str(Path.home() / ".vibe" / "observability"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._metrics: List[Metric] = []
        self._spans: List[Span] = []
        self._counters: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._gauges: Dict[str, float] = {}

        self._current_span: ContextVar[Optional[Span]] = ContextVar("current_span", default=None)

    @classmethod
    def get_default(cls) -> "Observability":
        """Return the global default instance (lazy-created)."""
        if cls._default_instance is None:
            cls._default_instance = cls()
        return cls._default_instance

    def reset(self):
        """Clear all metrics and spans. Call between independent eval runs."""
        self._metrics.clear()
        self._spans.clear()
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()

    # ─── Metrics API ───

    def counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None):
        key = self._metric_key(name, labels)
        self._counters[key] += value
        self._metrics.append(Metric(name, MetricType.COUNTER, self._counters[key], labels or {}))

    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None):
        key = self._metric_key(name, labels)
        self._gauges[key] = value
        self._metrics.append(Metric(name, MetricType.GAUGE, value, labels or {}))

    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None):
        key = self._metric_key(name, labels)
        self._histograms[key].append(value)
        self._metrics.append(Metric(name, MetricType.HISTOGRAM, value, labels or {}))

    def _metric_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    # ─── Tracing API ───

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        parent_id = parent.span_id if parent else None
        trace_id = parent.trace_id if parent else None

        if parent_id is None:
            current = self._current_span.get()
            if current:
                parent_id = current.span_id
                trace_id = current.trace_id

        if trace_id is None:
            trace_id = str(uuid.uuid4())

        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=str(uuid.uuid4())[:16],
            parent_id=parent_id,
            attributes=attributes or {},
        )
        self._spans.append(span)
        span._token = self._current_span.set(span)
        return span

    def finish_span(self, span: Span, status: str = "ok", error_message: Optional[str] = None):
        span.finish(status=status, error_message=error_message)
        try:
            self._current_span.reset(span._token)
        except (ValueError, AttributeError):
            # Token mismatch or missing token — fallback to manual parent search
            if span.parent_id:
                for s in reversed(self._spans):
                    if s.span_id == span.parent_id:
                        self._current_span.set(s)
                        return
            self._current_span.set(None)

    @contextmanager
    def span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """Context manager for creating a span."""
        span = self.start_span(name, attributes=attributes)
        try:
            yield span
            span.finish(status="ok")
        except Exception as e:
            span.finish(status="error", error_message=str(e))
            raise
        finally:
            self.finish_span(span)

    # ─── Export ───

    def export_metrics(self, path: Optional[str] = None) -> str:
        """Export metrics as JSON. Returns file path."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = path or str(self.output_dir / f"metrics_{timestamp}.json")

        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                k: {
                    "count": len(v),
                    "sum": sum(v),
                    "min": min(v) if v else 0,
                    "max": max(v) if v else 0,
                    "avg": sum(v) / len(v) if v else 0,
                    "p50": self._percentile(v, 0.5),
                    "p95": self._percentile(v, 0.95),
                    "p99": self._percentile(v, 0.99),
                }
                for k, v in self._histograms.items()
            },
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        return path

    def export_trace(self, path: Optional[str] = None) -> str:
        """Export trace spans as JSON. Returns file path."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = path or str(self.output_dir / f"trace_{timestamp}.json")

        data = {
            "trace_count": len(set(s.trace_id for s in self._spans)),
            "span_count": len(self._spans),
            "spans": [
                {
                    "name": s.name,
                    "trace_id": s.trace_id,
                    "span_id": s.span_id,
                    "parent_id": s.parent_id,
                    "duration_ms": s.duration_ms(),
                    "status": s.status,
                    "error_message": s.error_message,
                    "attributes": s.attributes,
                    "events": s.events,
                }
                for s in self._spans
            ],
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        return path

    def export_all(self, prefix: Optional[str] = None) -> Dict[str, str]:
        """Export both metrics and traces."""
        prefix = prefix or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return {
            "metrics": self.export_metrics(str(self.output_dir / f"metrics_{prefix}.json")),
            "trace": self.export_trace(str(self.output_dir / f"trace_{prefix}.json")),
        }

    def summary(self) -> Dict[str, Any]:
        """Return a quick summary of collected data."""
        return {
            "metrics_count": len(self._metrics),
            "span_count": len(self._spans),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histogram_keys": list(self._histograms.keys()),
        }

    @staticmethod
    def _percentile(values: List[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        k = (len(sorted_vals) - 1) * p
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_vals) else f
        if f == c:
            return sorted_vals[f]
        return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


# Global instance for easy import (shares identity with get_default)
obs = Observability.get_default()
