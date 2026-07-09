"""Circle packing benchmark evaluator and domain-aware mock generator."""

from __future__ import annotations

import json
import math
import random
from typing import Any, Callable

from vibe.evox.types import VariationOperator


def _default_circles(n: int = 10) -> list[tuple[float, float, float]]:
    """Generate a simple grid of non-overlapping circles as a baseline."""
    cols = max(1, int(math.sqrt(n)))
    spacing = 1.0 / (cols + 1)
    circles = []
    r = spacing / 2.5
    for i in range(cols):
        for j in range(cols):
            if len(circles) >= n:
                break
            x = (i + 1) * spacing
            y = (j + 1) * spacing
            circles.append((x, y, r))
    return circles


def circle_packing_evaluator(
    n: int = 10, box_size: float = 1.0
) -> "Callable[[str], tuple[float, dict[str, Any]]]":
    """Score a candidate by the packing density of circles in a unit square.

    The candidate content must be a JSON list of (x, y, r) tuples, e.g.:
        [[0.1, 0.1, 0.05], [0.3, 0.3, 0.05]]

    Score is the total valid circle area divided by the box area. Higher is better.
    Circles that overlap or extend outside the box are ignored.
    """

    def _eval(content: str) -> tuple[float, dict[str, Any]]:
        try:
            circles = json.loads(content)
            if not isinstance(circles, list):
                raise ValueError("content must be a JSON list")
        except Exception:
            return 0.0, {"valid": False, "reason": "invalid json", "density": 0.0}

        valid_circles = []
        for c in circles:
            if not isinstance(c, (list, tuple)) or len(c) != 3:
                continue
            try:
                x, y, r = float(c[0]), float(c[1]), float(c[2])
            except Exception:
                continue
            if r <= 0:
                continue
            if x - r < 0 or x + r > box_size or y - r < 0 or y + r > box_size:
                continue
            valid_circles.append((x, y, r))

        # Remove overlaps: keep larger circle, discard smaller overlapping ones
        valid_circles.sort(key=lambda c: c[2], reverse=True)
        kept = []
        for x, y, r in valid_circles:
            overlap = False
            for x2, y2, r2 in kept:
                dist = math.hypot(x - x2, y - y2)
                if dist < r + r2:
                    overlap = True
                    break
            if not overlap:
                kept.append((x, y, r))

        total_area = sum(math.pi * r * r for _, _, r in kept)
        density = total_area / (box_size * box_size)
        # Cap at 1.0 (theoretical max for square container)
        density = min(density, 1.0)
        return density, {
            "valid": True,
            "density": density,
            "kept": len(kept),
            "requested": len(circles),
        }

    return _eval


class CirclePackingMockGenerator:
    """Domain-aware mock generator for circle packing candidates.

    Candidates are JSON lists of (x, y, r) tuples. The generator supports:
    - local refinement: nudge one circle or change its radius
    - structural variation: replace with a grid or hexagonal pattern
    - free form: combine parent with inspiration circles
    """

    def __init__(self, n: int = 10, box_size: float = 1.0, seed: int | None = None):
        self.n = n
        self.box_size = box_size
        self.rng = random.Random(seed)

    async def generate(
        self,
        parent: str,
        operator: VariationOperator,
        inspiration: list[str],
        problem_description: str,
    ) -> str:
        try:
            circles = json.loads(parent)
            if not isinstance(circles, list):
                circles = []
        except Exception:
            circles = []

        if not circles:
            circles = self._random_circles()

        if operator == VariationOperator.LOCAL_REFINEMENT:
            circles = self._local_refine(circles)
        elif operator == VariationOperator.STRUCTURAL_VARIATION:
            circles = self._structural_variation(circles)
        else:
            # FREE_FORM: sometimes combine, sometimes refine
            if inspiration and self.rng.random() < 0.5:
                circles = self._combine(circles, inspiration)
            else:
                circles = self._local_refine(circles)

        return json.dumps(circles)

    def _random_circles(self) -> list[list[float]]:
        circles = []
        for _ in range(self.n):
            r = self.rng.uniform(0.03, 0.08)
            x = self.rng.uniform(r, self.box_size - r)
            y = self.rng.uniform(r, self.box_size - r)
            circles.append([x, y, r])
        return circles

    def _local_refine(self, circles: list[list[float]]) -> list[list[float]]:
        if not circles:
            return self._random_circles()
        circles = [list(c) for c in circles]
        idx = self.rng.randrange(len(circles))
        x, y, r = circles[idx]
        action = self.rng.choice(["move", "radius"])
        if action == "move":
            dx = self.rng.uniform(-0.05, 0.05)
            dy = self.rng.uniform(-0.05, 0.05)
            x = max(r, min(self.box_size - r, x + dx))
            y = max(r, min(self.box_size - r, y + dy))
        else:
            r = max(0.01, min(0.15, r + self.rng.uniform(-0.01, 0.01)))
            x = max(r, min(self.box_size - r, x))
            y = max(r, min(self.box_size - r, y))
        circles[idx] = [x, y, r]
        return circles

    def _structural_variation(self, circles: list[list[float]]) -> list[list[float]]:
        """Replace with a grid or hexagonal pattern, simulating a paradigm shift."""
        pattern = self.rng.choice(["grid", "hex", "random_dense"])
        if pattern == "grid":
            return self._grid_pattern()
        if pattern == "hex":
            return self._hex_pattern()
        # random_dense: many small circles
        return [
            [
                self.rng.uniform(0.05, self.box_size - 0.05),
                self.rng.uniform(0.05, self.box_size - 0.05),
                self.rng.uniform(0.02, 0.04),
            ]
            for _ in range(self.n)
        ]

    def _grid_pattern(self) -> list[list[float]]:
        cols = max(1, int(math.sqrt(self.n)))
        spacing = self.box_size / (cols + 1)
        r = spacing / 2.2
        circles = []
        for i in range(cols):
            for j in range(cols):
                if len(circles) >= self.n:
                    break
                circles.append([(i + 1) * spacing, (j + 1) * spacing, r])
        return circles

    def _hex_pattern(self) -> list[list[float]]:
        """Hexagonal close packing approximation."""
        r = self.box_size / (2 * math.sqrt(self.n) + 2)
        dx = 2 * r
        dy = r * math.sqrt(3)
        circles = []
        row = 0
        while len(circles) < self.n and dy * row < self.box_size - r:
            offset = r if row % 2 == 1 else 0
            col = 0
            while len(circles) < self.n:
                x = offset + r + col * dx
                y = r + row * dy
                if x + r > self.box_size:
                    break
                if y + r > self.box_size:
                    break
                circles.append([x, y, r])
                col += 1
            row += 1
        return circles

    def _combine(self, circles: list[list[float]], inspiration: list[str]) -> list[list[float]]:
        if not inspiration:
            return self._local_refine(circles)
        try:
            insp = json.loads(self.rng.choice(inspiration))
            if not isinstance(insp, list) or not insp:
                return self._local_refine(circles)
        except Exception:
            return self._local_refine(circles)

        # Replace a random subset with circles from inspiration
        circles = [list(c) for c in circles]
        k = min(len(circles) // 2 + 1, len(insp))
        idxs = self.rng.sample(range(len(circles)), k)
        for idx in idxs:
            src = insp[self.rng.randrange(len(insp))]
            circles[idx] = [float(src[0]), float(src[1]), float(src[2])]
        return circles
