"""Multi-objective scoring utilities for EvoX.

Mirrors the evaluation logic in SkyDiscover's AdaEvolve context builder:
- normalize metric values to a maximization direction
- collapse multiple objectives into a single progress proxy
"""

from __future__ import annotations

from typing import Any

PROGRESS_SCORE_MISSING = float("-inf")


def normalize_metric_value(
    metric_name: str, value: Any, higher_is_better: dict[str, bool]
) -> float | None:
    """Normalize a metric so higher is always better.

    If the configured direction for ``metric_name`` is ``False`` (lower is better),
    the value is negated.
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return None
    if not higher_is_better.get(metric_name, True):
        numeric = -numeric
    return numeric


def compute_proxy_score(
    metrics: dict[str, Any],
    *,
    fitness_key: str | None = None,
    pareto_objectives: list[str] | None = None,
    higher_is_better: dict[str, bool] | None = None,
) -> float:
    """Compute a scalar progress proxy from one or more objectives.

    Priority:
      1. If ``fitness_key`` is provided and present in ``metrics``, return it.
      2. If ``pareto_objectives`` is provided, average the normalized values.
      3. Otherwise average all numeric metrics.

    Missing objectives contribute 0.0 in the Pareto average so the proxy degrades
    gracefully when an objective is not reported.
    """
    direction = higher_is_better or {}

    if fitness_key and fitness_key in metrics:
        normalized = normalize_metric_value(fitness_key, metrics[fitness_key], direction)
        return normalized if normalized is not None else PROGRESS_SCORE_MISSING

    objectives = pareto_objectives or []
    if objectives:
        values: list[float] = []
        for obj in objectives:
            normalized = normalize_metric_value(obj, metrics.get(obj), direction)
            values.append(normalized if normalized is not None else 0.0)
        return sum(values) / len(values) if values else PROGRESS_SCORE_MISSING

    numeric_values = [
        float(v)
        for v in metrics.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if numeric_values:
        return sum(numeric_values) / len(numeric_values)

    return PROGRESS_SCORE_MISSING
