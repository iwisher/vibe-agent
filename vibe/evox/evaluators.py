"""Example evaluators for validating EvoX."""

from __future__ import annotations

from typing import Any, Callable


def string_match_evaluator(target: str) -> "Callable[[str], tuple[float, dict[str, Any]]]":
    """Score a candidate by negative Levenshtein distance to a target string.

    Higher score is better (closer to zero).
    """

    def _eval(content: str) -> tuple[float, dict[str, Any]]:
        distance = _levenshtein(content, target)
        score = -distance  # higher (less negative) is better
        return score, {"target": target, "distance": distance}

    return _eval


def expression_evaluator(target_value: float) -> "Callable[[str], tuple[float, dict[str, Any]]]":
    """Score a Python arithmetic expression by closeness to a target value.

    The candidate is expected to be a simple arithmetic expression using +, -, *, /, and numbers.
    """

    def _eval(content: str) -> tuple[float, dict[str, Any]]:
        if not content or not content.strip():
            # Empty expression gets a large but finite penalty
            value = 0.0
            error = abs(target_value) + 1e6
            return -error, {"target": target_value, "value": value, "error": error}
        try:
            # Safe evaluation of arithmetic expressions
            value = _safe_eval(content)
        except Exception:
            value = target_value + 1e6
        error = abs(value - target_value)
        score = -error
        return score, {"target": target_value, "value": value, "error": error}

    return _eval


def keyword_coverage_evaluator(
    keywords: list[str],
) -> "Callable[[str], tuple[float, dict[str, Any]]]":
    """Score a candidate by the fraction of required keywords it contains.

    Useful for prompt or program discovery.
    """

    def _eval(content: str) -> tuple[float, dict[str, Any]]:
        if not keywords:
            return 0.0, {}
        content_lower = content.lower()
        hits = sum(1 for kw in keywords if kw.lower() in content_lower)
        score = hits / len(keywords)
        return score, {"keywords": keywords, "hits": hits}

    return _eval


def toy_signal_filter_evaluator() -> "Callable[[str], tuple[float, dict[str, Any]]]":
    """Toy multi-objective evaluator inspired by the paper's signal-processing case study.

    Treats a candidate string as a filtering program and evaluates it on two
    competing objectives:
      - smoothness: longer, more repetitive programs are smoother
      - responsiveness: shorter, more varied programs are more responsive

    The combined score is the average, but the per-objective values are returned
    in artifacts["objectives"] so the loop can compute a Pareto proxy.
    """

    def _eval(content: str) -> tuple[float, dict[str, Any]]:
        if not content:
            return 0.0, {"objectives": {"smoothness": 0.0, "responsiveness": 0.0}}

        # Smoothness: prefer longer, more uniform content
        length = len(content)
        unique_ratio = len(set(content)) / max(1, length)
        smoothness = min(1.0, length / 50.0) * (1.0 - unique_ratio * 0.3)

        # Responsiveness: prefer shorter, more varied content
        responsiveness = (1.0 - min(1.0, length / 60.0)) * (0.3 + unique_ratio * 0.7)

        objectives = {"smoothness": smoothness, "responsiveness": responsiveness}
        combined = (smoothness + responsiveness) / 2.0
        return combined, {"objectives": objectives}

    return _eval


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        current_row = [i + 1]
        for j, cb in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (ca != cb)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _safe_eval(expr: str) -> float:
    """Safely evaluate a simple arithmetic expression.

    Allows only numbers and the operators +, -, *, /, parentheses, and whitespace.
    """
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expr):
        raise ValueError("Unsafe characters in expression")
    try:
        return float(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
    except Exception as e:
        raise ValueError(f"Invalid expression: {e}") from e
