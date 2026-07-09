"""Population state descriptor for EvoX strategy adaptation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from vibe.evox.types import Candidate


@dataclass
class PopulationDescriptor:
    """Summarizes the current state of the solution population.

    Mirrors the paper's φ(D_t): score statistics, frontier structure,
    progress indicators, recent-window statistics, and overuse patterns.
    """

    best_score: float
    median_score: float
    mean_score: float
    std_score: float
    min_score: float
    p25_score: float
    p75_score: float
    top_k_scores: list[float]
    steps_since_improvement: int
    recent_window_scores: list[float]
    recent_window_mean: float
    recent_window_improvement: float
    population_size: int
    diversity_proxy: float  # mean pairwise content-length difference
    selection_counts: dict[str, int]  # candidate id -> times selected as parent
    max_selection_ratio: float  # max(counts) / total selections
    overused_ids: list[str]  # ids with count > 2 * expected uniform frequency
    objective_stats: dict[str, dict[str, float | int]]  # per-objective best/mean/count stats

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_score": self.best_score,
            "median_score": self.median_score,
            "mean_score": self.mean_score,
            "std_score": self.std_score,
            "min_score": self.min_score,
            "p25_score": self.p25_score,
            "p75_score": self.p75_score,
            "top_k_scores": self.top_k_scores,
            "steps_since_improvement": self.steps_since_improvement,
            "recent_window_scores": self.recent_window_scores,
            "recent_window_mean": self.recent_window_mean,
            "recent_window_improvement": self.recent_window_improvement,
            "population_size": self.population_size,
            "diversity_proxy": self.diversity_proxy,
            "selection_counts": self.selection_counts,
            "max_selection_ratio": self.max_selection_ratio,
            "overused_ids": self.overused_ids,
            "objective_stats": self.objective_stats,
        }

    @classmethod
    def from_population(
        cls,
        population: list[Candidate],
        recent_window: list[Candidate],
        steps_since_improvement: int,
        top_k: int = 5,
        selection_counts: dict[str, int] | None = None,
    ) -> "PopulationDescriptor":
        counts = selection_counts or {}
        total_selections = max(1, sum(counts.values()))
        max_ratio = max(counts.values(), default=0) / total_selections
        expected_uniform = total_selections / max(1, len(population))
        overused = [cid for cid, cnt in counts.items() if cnt > 2 * expected_uniform]

        objective_stats = cls._compute_objective_stats(population)

        if not population:
            return cls(
                best_score=-math.inf,
                median_score=-math.inf,
                mean_score=-math.inf,
                std_score=0.0,
                min_score=-math.inf,
                p25_score=-math.inf,
                p75_score=-math.inf,
                top_k_scores=[],
                steps_since_improvement=steps_since_improvement,
                recent_window_scores=[],
                recent_window_mean=-math.inf,
                recent_window_improvement=0.0,
                population_size=0,
                diversity_proxy=0.0,
                selection_counts=counts,
                max_selection_ratio=max_ratio,
                overused_ids=overused,
                objective_stats=objective_stats,
            )

        scores = [c.score for c in population]
        sorted_scores = sorted(scores, reverse=True)
        n = len(scores)
        mean = sum(scores) / n
        variance = sum((s - mean) ** 2 for s in scores) / n
        std = math.sqrt(variance) if variance > 0 else 0.0

        def _percentile(vals: list[float], p: float) -> float:
            if not vals:
                return -math.inf
            k = (len(vals) - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return vals[int(k)]
            return vals[f] * (c - k) + vals[c] * (k - f)

        sorted_asc = sorted(scores)

        # Diversity proxy: average absolute difference in content lengths
        lengths = [len(c.content) for c in population]
        if len(lengths) > 1:
            diffs = [
                abs(lengths[i] - lengths[j])
                for i in range(len(lengths))
                for j in range(i + 1, len(lengths))
            ]
            diversity = sum(diffs) / len(diffs)
        else:
            diversity = 0.0

        recent_scores = [c.score for c in recent_window]
        recent_mean = sum(recent_scores) / len(recent_scores) if recent_scores else -math.inf
        recent_improvement = 0.0
        if len(recent_scores) >= 2:
            recent_improvement = recent_scores[-1] - recent_scores[0]

        return cls(
            best_score=sorted_scores[0],
            median_score=_percentile(sorted_asc, 0.5),
            mean_score=mean,
            std_score=std,
            min_score=sorted_scores[-1],
            p25_score=_percentile(sorted_asc, 0.25),
            p75_score=_percentile(sorted_asc, 0.75),
            top_k_scores=sorted_scores[:top_k],
            steps_since_improvement=steps_since_improvement,
            recent_window_scores=recent_scores,
            recent_window_mean=recent_mean,
            recent_window_improvement=recent_improvement,
            population_size=n,
            diversity_proxy=diversity,
            selection_counts=counts,
            max_selection_ratio=max_ratio,
            overused_ids=overused,
            objective_stats=objective_stats,
        )

    @staticmethod
    def _compute_objective_stats(
        population: list[Candidate],
    ) -> dict[str, dict[str, float]]:
        """Aggregate per-objective statistics from candidate objectives."""
        if not population:
            return {}
        objective_names = set()
        for c in population:
            objective_names.update(c.objectives.keys())
        if not objective_names:
            return {}

        stats: dict[str, dict[str, float]] = {}
        for name in objective_names:
            values = [c.objectives[name] for c in population if name in c.objectives]
            if not values:
                continue
            stats[name] = {
                "best": max(values),
                "worst": min(values),
                "mean": sum(values) / len(values),
                "count": len(values),
            }
        return stats
