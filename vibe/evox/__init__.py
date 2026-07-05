"""EvoX meta-evolution algorithm implementation for Vibe Agent.

Provides a minimal, self-contained implementation of the two-level evolution
process described in "EvoX: Meta-Evolution for Automated Discovery" (arXiv:2602.23413v1).

Public API:
    MetaEvolutionLoop: orchestrates solution evolution + strategy meta-evolution.
    Candidate: a scored solution candidate.
    SearchStrategy: selection + variation policy.
    StrategyRecord: deployed strategy with performance signal.
    PopulationDescriptor: population-state summary used for strategy adaptation.
"""

from vibe.evox.circle_packing import (
    CirclePackingMockGenerator,
    circle_packing_evaluator,
)
from vibe.evox.evaluators import (
    expression_evaluator,
    keyword_coverage_evaluator,
    string_match_evaluator,
)
from vibe.evox.generators import (
    LLMSolutionGenerator,
    LLMStrategyGenerator,
    MockSolutionGenerator,
)
from vibe.evox.loop import MetaEvolutionConfig, MetaEvolutionLoop
from vibe.evox.population import PopulationDescriptor
from vibe.evox.strategy import SearchStrategy, StrategyDatabase, StrategyRecord
from vibe.evox.types import Candidate, VariationOperator

__all__ = [
    "Candidate",
    "VariationOperator",
    "SearchStrategy",
    "StrategyRecord",
    "StrategyDatabase",
    "PopulationDescriptor",
    "LLMSolutionGenerator",
    "MockSolutionGenerator",
    "LLMStrategyGenerator",
    "MetaEvolutionLoop",
    "MetaEvolutionConfig",
    "string_match_evaluator",
    "expression_evaluator",
    "keyword_coverage_evaluator",
    "circle_packing_evaluator",
    "CirclePackingMockGenerator",
]
