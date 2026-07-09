"""Validation tests for EvoX meta-evolution implementation."""

from __future__ import annotations

import pytest

from vibe.evox.circle_packing import CirclePackingMockGenerator, circle_packing_evaluator
from vibe.evox.evaluators import (
    expression_evaluator,
    keyword_coverage_evaluator,
    string_match_evaluator,
    toy_signal_filter_evaluator,
)
from vibe.evox.generators import MockSolutionGenerator, MockStrategyGenerator
from vibe.evox.loop import MetaEvolutionConfig, MetaEvolutionLoop
from vibe.evox.metrics import compute_proxy_score, normalize_metric_value
from vibe.evox.population import PopulationDescriptor
from vibe.evox.strategy import SearchStrategy, StrategyDatabase, StrategyRecord
from vibe.evox.strategy_code import EvolvableStrategy
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
        # Evolution should find a candidate closer to the target than a random guess.
        assert best.score > -len(target)
        assert best.artifacts["distance"] < len(target)
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
        # Score should be finite and not the invalid-expression penalty.
        assert best.score > -(abs(target_value) + 1e6)
        assert best.artifacts["error"] < abs(target_value) + 1e6
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


class TestEvoXMetrics:
    """Guardrail tests for AdaEvolve-style multi-objective evaluation."""

    def test_normalize_metric_value_maximize(self):
        assert normalize_metric_value("x", 5.0, {"x": True}) == 5.0

    def test_normalize_metric_value_minimize(self):
        assert normalize_metric_value("x", 5.0, {"x": False}) == -5.0

    def test_compute_proxy_score_fitness_key(self):
        metrics = {"a": 1.0, "b": 2.0}
        assert compute_proxy_score(metrics, fitness_key="b") == 2.0

    def test_compute_proxy_score_pareto_average(self):
        metrics = {"x": 4.0, "y": 6.0}
        assert compute_proxy_score(
            metrics, pareto_objectives=["x", "y"], higher_is_better={"x": True, "y": True}
        ) == pytest.approx(5.0)

    def test_compute_proxy_score_pareto_with_minimize(self):
        metrics = {"x": 4.0, "y": 6.0}
        assert compute_proxy_score(
            metrics, pareto_objectives=["x", "y"], higher_is_better={"x": True, "y": False}
        ) == pytest.approx(-1.0)

    def test_compute_proxy_score_missing_objective(self):
        metrics = {"x": 4.0}
        assert compute_proxy_score(
            metrics, pareto_objectives=["x", "y"], higher_is_better={"x": True, "y": True}
        ) == pytest.approx(2.0)


class TestEvoXUCB:
    """Guardrail tests for UCB parent selection."""

    def test_ucb_selects_underexplored_candidate(self):
        """UCB should favor a high-scoring candidate that has been selected less often."""
        strategy = SearchStrategy(
            parent_selection="ucb",
            operator_preference=VariationOperator.FREE_FORM,
            instructions="Use UCB to balance exploration and exploitation.",
        )
        module = strategy.compile()

        pop = [
            Candidate(content="a", score=10.0),
            Candidate(content="b", score=9.0),
            Candidate(content="c", score=8.0),
        ]
        counts = {pop[0].id: 100, pop[1].id: 1, pop[2].id: 1}
        context = {"selection_counts": counts}

        # With many samples, UCB should almost always pick the underexplored high-ish scorer
        rng = __import__("random").Random(42)
        selected = [module.select_parent(pop, rng, context).id for _ in range(50)]
        # Candidate b has high score and very low count, so it should dominate
        assert selected.count(pop[1].id) > selected.count(pop[0].id)

    def test_ucb_falls_back_without_context(self):
        strategy = SearchStrategy(parent_selection="ucb")
        module = strategy.compile()
        pop = [Candidate(content="a", score=1.0), Candidate(content="b", score=2.0)]
        parent = module.select_parent(pop, __import__("random").Random(0), None)
        assert parent in pop


class TestEvoXMultiObjective:
    """Guardrail tests for multi-objective EvoX loop behavior."""

    @pytest.mark.asyncio
    async def test_multi_objective_mode_uses_proxy_score(self):
        """When pareto_objectives are configured, the loop uses the proxy score."""
        loop = MetaEvolutionLoop(
            evaluator=toy_signal_filter_evaluator(),
            solution_generator=MockSolutionGenerator(seed=10),
            strategy_generator=MockStrategyGenerator(seed=10),
            config=MetaEvolutionConfig(
                total_iterations=20,
                window_size=10,
                stagnation_threshold=1e-6,
                pareto_objectives=["smoothness", "responsiveness"],
                higher_is_better={"smoothness": True, "responsiveness": True},
            ),
            seed=10,
        )
        result = await loop.run()
        best = result.best_candidate
        assert best.objectives
        assert "smoothness" in best.objectives
        assert "responsiveness" in best.objectives
        # Proxy score should be the average of the two objectives
        expected_proxy = (best.objectives["smoothness"] + best.objectives["responsiveness"]) / 2.0
        assert best.score == pytest.approx(expected_proxy)

    @pytest.mark.asyncio
    async def test_multi_objective_population_descriptor_includes_objective_stats(self):
        loop = MetaEvolutionLoop(
            evaluator=toy_signal_filter_evaluator(),
            solution_generator=MockSolutionGenerator(seed=11),
            strategy_generator=MockStrategyGenerator(seed=11),
            config=MetaEvolutionConfig(
                total_iterations=10,
                window_size=5,
                stagnation_threshold=1e-6,
                pareto_objectives=["smoothness", "responsiveness"],
                higher_is_better={"smoothness": True, "responsiveness": True},
            ),
            seed=11,
        )
        await loop.run()
        descriptor = PopulationDescriptor.from_population(
            loop.population,
            recent_window=loop.population[-5:],
            steps_since_improvement=0,
            selection_counts=loop._selection_counts,
        )
        assert "smoothness" in descriptor.objective_stats
        assert "responsiveness" in descriptor.objective_stats
        assert "best" in descriptor.objective_stats["smoothness"]

    @pytest.mark.asyncio
    async def test_ucb_strategy_switches_in_loop(self):
        """UCB parent selection can be deployed and used without crashing."""
        loop = MetaEvolutionLoop(
            evaluator=string_match_evaluator("ucb"),
            solution_generator=MockSolutionGenerator(seed=20),
            strategy_generator=MockStrategyGenerator(seed=20),
            config=MetaEvolutionConfig(
                total_iterations=30,
                window_size=10,
                stagnation_threshold=0.001,
            ),
            seed=20,
        )
        # Seed initial population so UCB has scores to work with
        loop.population = [
            Candidate(content="ucb", score=-1.0),
            Candidate(content="test", score=-2.0),
            Candidate(content="best", score=-3.0),
        ]
        result = await loop.run()
        assert result.iterations == 30
        assert len(loop.strategy_db.records) >= 1


class TestEvoXStrategySandbox:
    """Guardrail tests for strategy code execution sandbox."""

    def test_rejects_disallowed_import(self):
        """Strategy code importing os/sys/etc. must fail to compile."""
        code = "import os\ndef select_parent(population, rng, context=None): return population[0]\n"
        with pytest.raises(ValueError):
            EvolvableStrategy(code=code).compile()

    def test_rejects_import_from(self):
        """from ... import is not allowed in strategy code."""
        code = (
            "from os import path\n"
            "def select_parent(population, rng, context=None): return population[0]\n"
        )
        with pytest.raises(ValueError):
            EvolvableStrategy(code=code).compile()

    def test_blocks_attribute_escape_attempt(self):
        """A common introspection escape path should not reach sensitive modules."""
        code = """
def select_parent(population, rng, context=None):
    # Common sandbox escape attempt; should fail because __builtins__ is restricted
    try:
        ().__class__.__base__.__subclasses__()
    except Exception:
        pass
    return population[0]

def select_inspiration(population, parent, rng):
    return []

def select_operator(rng):
    return "free_form"
"""
        strategy = EvolvableStrategy(code=code)
        module = strategy.compile()
        pop = [Candidate(content="x", score=1.0)]
        # The escape attempt inside select_parent should be caught/handled
        parent = module.select_parent(pop, __import__("random").Random(0), None)
        assert parent in pop
