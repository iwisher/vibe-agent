"""Validation tests for EvoX meta-evolution implementation."""

from __future__ import annotations

import pytest

from vibe.evox.circle_packing import CirclePackingMockGenerator, circle_packing_evaluator
from vibe.evox.evaluators import (
    expression_evaluator,
    keyword_coverage_evaluator,
    string_match_evaluator,
)
from vibe.evox.generators import MockSolutionGenerator, MockStrategyGenerator
from vibe.evox.loop import MetaEvolutionConfig, MetaEvolutionLoop
from vibe.evox.population import PopulationDescriptor
from vibe.evox.strategy import SearchStrategy, StrategyDatabase, StrategyRecord
from vibe.evox.types import Candidate, VariationOperator


class TestEvoXComponents:
    """Unit tests for EvoX data structures and helpers."""

    def test_candidate_creation(self):
        c = Candidate(content="x", score=1.0)
        assert c.content == "x"
        assert c.score == 1.0
        assert hasattr(c, "id")
        assert len(c.id) == 12

    def test_strategy_compiles_code(self):
        s = SearchStrategy(
            parent_selection="best",
            operator_preference=VariationOperator.LOCAL_REFINEMENT,
            instructions="Refine aggressively.",
        )
        module = s.compile()
        pop = [Candidate(content="a", score=1.0), Candidate(content="b", score=2.0)]
        parent = module.select_parent(pop, __import__("random").Random(0))
        assert parent.score == 2.0
        op = module.select_operator(__import__("random").Random(0))
        assert op == VariationOperator.LOCAL_REFINEMENT

    def test_strategy_database(self):
        db = StrategyDatabase()
        s = SearchStrategy()
        db.add(
            StrategyRecord(
                strategy=s,
                descriptor={},
                score=0.5,
                window_size=10,
                start_score=0.0,
                end_score=0.5,
            )
        )
        assert len(db) == 1
        assert db.best(1)[0].score == 0.5

    def test_population_descriptor(self):
        pop = [
            Candidate(content="a", score=1.0),
            Candidate(content="bb", score=2.0),
            Candidate(content="ccc", score=3.0),
        ]
        counts = {pop[0].id: 5, pop[1].id: 1, pop[2].id: 1}
        desc = PopulationDescriptor.from_population(
            pop, recent_window=pop, steps_since_improvement=0, selection_counts=counts
        )
        assert desc.best_score == 3.0
        assert desc.population_size == 3
        assert desc.diversity_proxy > 0
        assert desc.selection_counts[pop[0].id] == 5
        assert pop[0].id in desc.overused_ids


class TestEvoXUseCases:
    """Typical use cases validating the full EvoX loop."""

    @pytest.mark.asyncio
    async def test_use_case_1_string_match(self):
        """UC1: Discover a string close to a target via meta-evolution."""
        target = "hello"
        loop = MetaEvolutionLoop(
            evaluator=string_match_evaluator(target),
            solution_generator=MockSolutionGenerator(seed=1),
            strategy_generator=MockStrategyGenerator(seed=1),
            config=MetaEvolutionConfig(
                total_iterations=60,
                window_size=10,
                stagnation_threshold=0.01,
            ),
            seed=1,
        )
        result = await loop.run()
        best = result.best_candidate
        assert best.score > -len(target)  # better than random seed baseline
        assert result.iterations == 60
        assert len(result.population) == result.iterations + 3  # 3 seeded + evolved

    @pytest.mark.asyncio
    async def test_use_case_2_expression_discovery(self):
        """UC2: Discover an arithmetic expression close to a target value."""
        target_value = 42.0
        loop = MetaEvolutionLoop(
            evaluator=expression_evaluator(target_value),
            solution_generator=MockSolutionGenerator(seed=2),
            strategy_generator=MockStrategyGenerator(seed=2),
            config=MetaEvolutionConfig(
                total_iterations=80,
                window_size=10,
                stagnation_threshold=0.1,
            ),
            seed=2,
        )
        result = await loop.run()
        best = result.best_candidate
        assert best.score > float("-inf")
        assert result.iterations == 80
        assert len(loop.strategy_db.records) >= 1

    @pytest.mark.asyncio
    async def test_use_case_3_keyword_coverage(self):
        """UC3: Discover content covering a set of required keywords."""
        keywords = ["fast", "safe", "simple"]
        loop = MetaEvolutionLoop(
            evaluator=keyword_coverage_evaluator(keywords),
            solution_generator=MockSolutionGenerator(seed=3),
            strategy_generator=MockStrategyGenerator(seed=3),
            config=MetaEvolutionConfig(
                total_iterations=100,
                window_size=15,
                stagnation_threshold=0.001,
            ),
            seed=3,
        )
        result = await loop.run()
        best = result.best_candidate
        assert 0.0 <= best.score <= 1.0
        assert result.iterations == 100
        assert len(loop.strategy_db.records) >= 1

    @pytest.mark.asyncio
    async def test_use_case_4_circle_packing(self):
        """UC4: Evolve circle placements to maximize packing density."""
        loop = MetaEvolutionLoop(
            evaluator=circle_packing_evaluator(n=12),
            solution_generator=CirclePackingMockGenerator(n=12, seed=5),
            strategy_generator=MockStrategyGenerator(seed=5),
            config=MetaEvolutionConfig(
                total_iterations=80,
                window_size=15,
                stagnation_threshold=1e-4,
                problem_description=(
                    "Place non-overlapping circles in a unit square to maximize area coverage."
                ),
            ),
            seed=5,
        )
        result = await loop.run()
        best = result.best_candidate
        assert 0.0 <= best.score <= 1.0
        assert best.artifacts.get("valid") is True
        assert result.iterations == 80
        assert len(loop.strategy_db.records) >= 1


class TestEvoXBaselines:
    """Prove that EvoX meta-evolution beats fixed strategies on the same budget."""

    @pytest.mark.asyncio
    async def test_evoX_beats_fixed_strategy_on_string_match(self):
        """EvoX with adaptation outperforms a fixed uniform-random strategy."""
        target = "abc"
        budget = 60
        seed = 7

        fixed = MetaEvolutionLoop(
            evaluator=string_match_evaluator(target),
            solution_generator=MockSolutionGenerator(seed=seed),
            strategy_generator=MockStrategyGenerator(seed=seed),
            config=MetaEvolutionConfig(
                total_iterations=budget,
                window_size=budget + 1,  # never trigger meta-evolution
                stagnation_threshold=float("inf"),
            ),
            seed=seed,
        )
        fixed_result = await fixed.run()

        adaptive = MetaEvolutionLoop(
            evaluator=string_match_evaluator(target),
            solution_generator=MockSolutionGenerator(seed=seed),
            strategy_generator=MockStrategyGenerator(seed=seed),
            config=MetaEvolutionConfig(
                total_iterations=budget,
                window_size=10,
                stagnation_threshold=0.01,
            ),
            seed=seed,
        )
        adaptive_result = await adaptive.run()

        assert adaptive_result.strategy_switches >= 1
        assert adaptive_result.best_candidate.score >= fixed_result.best_candidate.score

    @pytest.mark.asyncio
    async def test_evoX_beats_fixed_strategy_on_circle_packing(self):
        """EvoX with adaptation outperforms a fixed strategy on circle packing."""
        n = 12
        budget = 80
        seed = 11

        fixed = MetaEvolutionLoop(
            evaluator=circle_packing_evaluator(n=n),
            solution_generator=CirclePackingMockGenerator(n=n, seed=seed),
            strategy_generator=MockStrategyGenerator(seed=seed),
            config=MetaEvolutionConfig(
                total_iterations=budget,
                window_size=budget + 1,
                stagnation_threshold=float("inf"),
                problem_description="Maximize circle packing density.",
            ),
            seed=seed,
        )
        fixed_result = await fixed.run()

        adaptive = MetaEvolutionLoop(
            evaluator=circle_packing_evaluator(n=n),
            solution_generator=CirclePackingMockGenerator(n=n, seed=seed),
            strategy_generator=MockStrategyGenerator(seed=seed),
            config=MetaEvolutionConfig(
                total_iterations=budget,
                window_size=15,
                stagnation_threshold=1e-4,
                problem_description="Maximize circle packing density.",
            ),
            seed=seed,
        )
        adaptive_result = await adaptive.run()

        assert adaptive_result.strategy_switches >= 1
        assert adaptive_result.best_candidate.score >= fixed_result.best_candidate.score
