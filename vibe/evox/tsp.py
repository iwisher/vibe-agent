"""Traveling Salesman Problem (TSP) evaluator and generator for EvoX.

TSP is a representative complex case: structured JSON candidates, hard
constraints (valid permutation), and an NP-hard optimization landscape.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any, Callable


def _generate_random_cities(n: int, seed: int | None = None) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    return [(rng.random(), rng.random()) for _ in range(n)]


def _tour_distance(tour: list[int], cities: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(tour)):
        a = cities[tour[i]]
        b = cities[tour[(i + 1) % len(tour)]]
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        total += math.hypot(dx, dy)
    return total


def _is_valid_tour(tour: list[int], n: int) -> bool:
    return isinstance(tour, list) and len(tour) == n and set(tour) == set(range(n))


def tsp_evaluator(
    n: int = 10, seed: int | None = None
) -> "Callable[[str], tuple[float, dict[str, Any]]]":
    """Score a TSP candidate by negative total tour length.

    Candidates are JSON arrays of city indices (a permutation of 0..n-1).
    Higher score is better (shorter tour).
    """
    cities = _generate_random_cities(n, seed)

    def _eval(content: str) -> tuple[float, dict[str, Any]]:
        try:
            tour = json.loads(content)
        except Exception:
            return -1e9, {"valid": False, "error": "invalid JSON"}

        if not _is_valid_tour(tour, n):
            return -1e9, {"valid": False, "error": "not a valid permutation"}

        distance = _tour_distance(tour, cities)
        # Normalize score so shorter tours are closer to 0 and typical range is [-1, 0]
        max_possible = math.sqrt(2) * n
        score = -distance / max_possible
        return score, {"valid": True, "distance": distance, "n": n}

    return _eval


class TSPMockGenerator:
    """Domain-aware mock generator for TSP tours.

    Supports:
    - 2-opt local refinement (swap two edges)
    - structural variation (segment reversal / insertion)
    - free-form combination using inspiration tours
    """

    def __init__(self, n: int = 10, seed: int | None = None):
        self.n = n
        self.rng = random.Random(seed)

    def _random_tour(self) -> str:
        tour = list(range(self.n))
        self.rng.shuffle(tour)
        return json.dumps(tour)

    def _parse(self, content: str) -> list[int]:
        try:
            tour = json.loads(content)
            if _is_valid_tour(tour, self.n):
                return tour
        except Exception:
            pass
        return list(range(self.n))

    def _two_opt(self, tour: list[int]) -> list[int]:
        if self.n < 4:
            return tour
        i = self.rng.randrange(self.n - 1)
        j = self.rng.randrange(i + 1, self.n)
        # Reverse segment between i+1 and j
        new_tour = tour[: i + 1] + tour[i + 1 : j + 1][::-1] + tour[j + 1 :]
        return new_tour

    def _segment_move(self, tour: list[int]) -> list[int]:
        if self.n < 3:
            return tour
        length = self.rng.randint(1, max(1, self.n // 3))
        start = self.rng.randrange(self.n - length + 1)
        segment = tour[start : start + length]
        remaining = tour[:start] + tour[start + length :]
        insert_at = self.rng.randrange(len(remaining) + 1)
        return remaining[:insert_at] + segment + remaining[insert_at:]

    async def generate(
        self,
        parent: str,
        operator: Any,
        inspiration: list[str],
        problem_description: str,
    ) -> str:
        tour = self._parse(parent)

        if operator.value == "local_refinement":
            return json.dumps(self._two_opt(tour))

        if operator.value == "structural_variation":
            return json.dumps(self._segment_move(tour))

        # FREE_FORM: combine with inspiration or apply random perturbation
        if inspiration:
            insp = self._parse(self.rng.choice(inspiration))
            # Order crossover (OX)-like: take a random segment from parent,
            # fill remaining positions in order from inspiration
            start = self.rng.randrange(self.n)
            end = self.rng.randrange(start, self.n)
            segment = set(tour[start:end])
            child = [None] * self.n
            child[start:end] = tour[start:end]
            fill = [c for c in insp if c not in segment]
            idx = 0
            for i in range(self.n):
                if child[i] is None:
                    child[i] = fill[idx]
                    idx += 1
            return json.dumps(child)

        # Fallback: random 2-opt with probability, else segment move
        if self.rng.random() < 0.5:
            return json.dumps(self._two_opt(tour))
        return json.dumps(self._segment_move(tour))
