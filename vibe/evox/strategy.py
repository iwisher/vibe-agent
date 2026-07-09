"""Search strategy representation and strategy database for EvoX."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from vibe.evox.strategy_code import EvolvableStrategy
from vibe.evox.types import VariationOperator


@dataclass
class SearchStrategy:
    """A search strategy governs how the next candidate is generated.

    In the full EvoX implementation the strategy is represented as executable
    Python source code. The legacy configuration fields are retained for
    convenience and are used to synthesize the initial strategy code when
    `code` is not provided.
    """

    # Legacy configuration fields (used to build default code)
    parent_selection: str = "uniform_random"
    inspiration_selection: str = "none"
    operator_preference: VariationOperator | None = None
    operator_weights: dict[str, float] = field(default_factory=dict)
    instructions: str = ""

    # Evolvable code representation
    code: str = ""
    description: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self):
        if not self.code:
            self.code = self._synthesize_code()
        if not self.description:
            self.description = f"Strategy using {self.parent_selection} parent selection"
        if not self.operator_weights:
            self.operator_weights = {
                VariationOperator.LOCAL_REFINEMENT: 0.33,
                VariationOperator.STRUCTURAL_VARIATION: 0.33,
                VariationOperator.FREE_FORM: 0.34,
            }
        self._evolvable = EvolvableStrategy(
            code=self.code, description=self.description, id=self.id
        )

    def _synthesize_code(self) -> str:
        """Generate Python strategy code from legacy configuration fields."""
        op_pref = self.operator_preference.value if self.operator_preference else None
        if op_pref:
            operator_body = f'    return "{op_pref}"'
        else:
            weights = {
                "local_refinement": self.operator_weights.get(
                    VariationOperator.LOCAL_REFINEMENT, 0.33
                ),
                "structural_variation": self.operator_weights.get(
                    VariationOperator.STRUCTURAL_VARIATION, 0.33
                ),
                "free_form": self.operator_weights.get(VariationOperator.FREE_FORM, 0.34),
            }
            operator_body = (
                f"    operators = {list(weights.keys())}\n"
                f"    weights = {list(weights.values())}\n"
                "    return rng.choices(operators, weights=weights, k=1)[0]"
            )

        return f'''\
def select_parent(population, rng, context=None):
    """{self.instructions or "Select a parent using " + self.parent_selection}"""
    if not population:
        raise ValueError("empty population")
    if "{self.parent_selection}" == "best":
        return max(population, key=lambda c: c.score)
    if "{self.parent_selection}" == "uniform_random":
        return rng.choice(population)
    if "{self.parent_selection}" == "tournament":
        size = min(3, len(population))
        return max(rng.sample(population, size), key=lambda c: c.score)
    if "{self.parent_selection}" == "diverse":
        sorted_pop = sorted(population, key=lambda c: c.score, reverse=True)
        tier = rng.randrange(4)
        start = tier * len(sorted_pop) // 4
        end = max((tier + 1) * len(sorted_pop) // 4, start + 1)
        return rng.choice(sorted_pop[start:end])
    if "{self.parent_selection}" == "ucb":
        import math
        if context is None:
            return rng.choice(population)
        counts = context.get("selection_counts", {{}})
        total = max(1, sum(counts.values()))
        def _ucb_score(c):
            exploit = c.score
            count = counts.get(c.id, 0)
            explore = math.sqrt(2.0 * math.log(total) / (count + 1))
            return exploit + explore
        return max(population, key=_ucb_score)
    # default
    return rng.choice(population)


def select_inspiration(population, parent, rng):
    """Build an inspiration set."""
    if "{self.inspiration_selection}" == "none" or len(population) <= 1:
        return []
    others = [c for c in population if c.id != parent.id]
    if not others:
        return []
    if "{self.inspiration_selection}" == "uniform_random":
        k = min(2, len(others))
        return [c.content for c in rng.sample(others, k)]
    if "{self.inspiration_selection}" == "diverse":
        sorted_others = sorted(others, key=lambda c: c.score, reverse=True)
        k = min(3, len(sorted_others))
        step = max(1, len(sorted_others) // k)
        return [sorted_others[i * step].content for i in range(k)]
    if "{self.inspiration_selection}" == "frontier":
        top = sorted(others, key=lambda c: c.score, reverse=True)[:5]
        return [c.content for c in top]
    return []


def select_operator(rng):
    """Choose a variation operator."""
{operator_body}
'''

    def compile(self):
        """Compile the strategy code into an executable module."""
        return self._evolvable.compile()

    def to_prompt_text(self) -> str:
        """Serialize strategy into a short natural-language directive."""
        return self._evolvable.to_prompt_text()

    def copy(self) -> "SearchStrategy":
        return SearchStrategy(
            parent_selection=self.parent_selection,
            inspiration_selection=self.inspiration_selection,
            operator_preference=self.operator_preference,
            operator_weights=copy.deepcopy(self.operator_weights),
            instructions=self.instructions,
            code=self.code,
            description=self.description,
        )


@dataclass
class StrategyRecord:
    """A deployed strategy together with the population descriptor and score signal."""

    strategy: SearchStrategy
    descriptor: dict[str, Any]
    score: float
    window_size: int
    start_score: float
    end_score: float
    deployed_at: int = 0


class StrategyDatabase:
    """Memory of previously deployed search strategies."""

    def __init__(self):
        self.records: list[StrategyRecord] = []

    def add(self, record: StrategyRecord) -> None:
        self.records.append(record)

    def best(self, k: int = 1) -> list[StrategyRecord]:
        """Return top-k records by score signal."""
        sorted_records = sorted(self.records, key=lambda r: r.score, reverse=True)
        return sorted_records[:k]

    def __len__(self) -> int:
        return len(self.records)
