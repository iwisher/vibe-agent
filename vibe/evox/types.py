"""Core data types for EvoX meta-evolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class VariationOperator(str, Enum):
    """Variation operators controlling the type of edit requested from the generator."""

    LOCAL_REFINEMENT = "local_refinement"
    STRUCTURAL_VARIATION = "structural_variation"
    FREE_FORM = "free_form"


@dataclass
class Candidate:
    """A single candidate solution with its evaluation outcome."""

    content: str
    score: float
    artifacts: dict[str, Any] = field(default_factory=dict)
    generation: int = 0
    parent_id: str | None = None
    strategy_id: str | None = None
    operator: VariationOperator | None = None

    def __post_init__(self):
        if not hasattr(self, "id"):
            import uuid

            object.__setattr__(self, "id", uuid.uuid4().hex[:12])


# Evaluator: maps candidate content -> (score, artifacts)
Evaluator = Callable[[str], tuple[float, dict[str, Any]]]
