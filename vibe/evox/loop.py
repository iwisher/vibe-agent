"""EvoX meta-evolution loop implementation."""

from __future__ import annotations

import copy
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Protocol

from vibe.evox.metrics import PROGRESS_SCORE_MISSING, compute_proxy_score
from vibe.evox.population import PopulationDescriptor
from vibe.evox.strategy import SearchStrategy, StrategyDatabase, StrategyRecord
from vibe.evox.types import Candidate, Evaluator, VariationOperator

logger = logging.getLogger(__name__)


class SolutionGenerator(Protocol):
    async def generate(
        self,
        parent: str,
        operator: VariationOperator,
        inspiration: list[str],
        problem_description: str,
    ) -> str: ...


class StrategyGenerator(Protocol):
    async def mutate(
        self,
        parent_strategy: SearchStrategy,
        descriptor: PopulationDescriptor,
        history: list[StrategyRecord],
    ) -> SearchStrategy: ...


@dataclass
class MetaEvolutionConfig:
    """Hyperparameters for the EvoX loop."""

    total_iterations: int = 100
    window_size: int = 10
    stagnation_threshold: float = 1e-6
    max_strategy_retries: int = 3
    validation_trials: int = 3
    problem_description: str = "Optimize the candidate solution."

    # Multi-objective evaluation (matches AdaEvolve builder.py semantics)
    pareto_objectives: list[str] = field(default_factory=list)
    higher_is_better: dict[str, bool] = field(default_factory=dict)
    fitness_key: str | None = None


@dataclass
class MetaEvolutionResult:
    """Result of a full EvoX run."""

    best_candidate: Candidate
    population: list[Candidate]
    strategy_history: list[StrategyRecord]
    iterations: int
    strategy_switches: int


class MetaEvolutionLoop:
    """Two-level evolution loop: solution evolution + meta-evolution of strategies."""

    def __init__(
        self,
        evaluator: Evaluator,
        solution_generator: SolutionGenerator,
        strategy_generator: StrategyGenerator,
        config: MetaEvolutionConfig | None = None,
        initial_candidates: list[Candidate] | None = None,
        initial_strategy: SearchStrategy | None = None,
        seed: int | None = None,
    ):
        self.evaluator = evaluator
        self.solution_generator = solution_generator
        self.strategy_generator = strategy_generator
        self.config = config or MetaEvolutionConfig()
        self.rng = random.Random(seed)

        self.population: list[Candidate] = list(initial_candidates or [])
        self.strategy_db = StrategyDatabase()
        self.current_strategy = initial_strategy or self._initial_strategy()
        self.strategy_switches = 0
        self.steps_since_improvement = 0
        self.iteration = 0
        self._selection_counts: dict[str, int] = {}

    def _initial_strategy(self) -> SearchStrategy:
        return SearchStrategy(
            parent_selection="uniform_random",
            inspiration_selection="none",
            operator_preference=VariationOperator.FREE_FORM,
            instructions="Start by exploring the space uniformly at random.",
        )

    def _record_selection(self, candidate: Candidate) -> None:
        self._selection_counts[candidate.id] = self._selection_counts.get(candidate.id, 0) + 1

    # ────────────────────────────────
    # Evolution step
    # ────────────────────────────────

    async def _evolve_one(self) -> Candidate:
        module = self.current_strategy.compile()
        context = {"selection_counts": self._selection_counts}
        parent = module.select_parent(self.population, self.rng, context)
        self._record_selection(parent)
        operator = module.select_operator(self.rng)
        inspiration = module.select_inspiration(self.population, parent, self.rng)

        content = await self.solution_generator.generate(
            parent=parent.content,
            operator=operator,
            inspiration=inspiration,
            problem_description=self.config.problem_description,
        )
        score, artifacts = self.evaluator(content)
        objectives = artifacts.get("objectives", {}) if isinstance(artifacts, dict) else {}
        score = self._resolve_score(score, objectives)

        candidate = Candidate(
            content=content,
            score=score,
            artifacts=artifacts,
            objectives=objectives,
            generation=self.iteration,
            parent_id=parent.id,
            strategy_id=self.current_strategy.id,
            operator=operator,
        )
        self.population.append(candidate)
        return candidate

    def _resolve_score(self, raw_score: float, objectives: dict[str, float]) -> float:
        """Use AdaEvolve-style proxy score when objectives are configured."""
        if not self.config.pareto_objectives:
            return raw_score
        proxy = compute_proxy_score(
            objectives,
            fitness_key=self.config.fitness_key,
            pareto_objectives=self.config.pareto_objectives,
            higher_is_better=self.config.higher_is_better,
        )
        return proxy if proxy != PROGRESS_SCORE_MISSING else raw_score

    def _strategy_score(self, start_score: float, end_score: float, window_size: int) -> float:
        """J(S) = (s_end - s_start) * log(1 + s_start) / sqrt(W)."""
        delta = end_score - start_score
        if start_score == -math.inf:
            return delta
        return delta * math.log1p(max(start_score, 0.0)) / math.sqrt(window_size)

    async def _validate_strategy(self, strategy: SearchStrategy) -> bool:
        """Validate that the strategy code compiles and produces valid candidates.

        Validation trials do not consume the main evaluation budget or permanently
        alter the population.
        """
        if not self.population:
            return False
        try:
            module = strategy.compile()
            context = {"selection_counts": self._selection_counts}
            for _ in range(self.config.validation_trials):
                parent = module.select_parent(self.population, self.rng, context)
                operator = module.select_operator(self.rng)
                inspiration = module.select_inspiration(self.population, parent, self.rng)
                content = await self.solution_generator.generate(
                    parent=parent.content,
                    operator=operator,
                    inspiration=inspiration,
                    problem_description=self.config.problem_description,
                )
                score, artifacts = self.evaluator(content)
                objectives = artifacts.get("objectives", {}) if isinstance(artifacts, dict) else {}
                score = self._resolve_score(score, objectives)
                if not math.isfinite(score):
                    return False
            return True
        except Exception:
            logger.warning("Strategy validation failed for strategy %s", strategy.id, exc_info=True)
            return False

    async def _meta_evolve_strategy(self) -> SearchStrategy:
        """Evolve a new search strategy from the strategy database."""
        descriptor = PopulationDescriptor.from_population(
            self.population,
            recent_window=self.population[-self.config.window_size :],
            steps_since_improvement=self.steps_since_improvement,
            selection_counts=self._selection_counts,
        )

        # Parent strategy: score-biased selection from history
        if self.strategy_db.records:
            records = self.strategy_db.best(k=len(self.strategy_db.records))
            weights = [max(r.score, 1e-6) for r in records]
            total = sum(weights)
            probs = [w / total for w in weights]
            parent_record = self.rng.choices(records, weights=probs, k=1)[0]
            parent_strategy = parent_record.strategy
        else:
            parent_strategy = self.current_strategy

        # Try generating a valid strategy
        for _ in range(self.config.max_strategy_retries):
            new_strategy = await self.strategy_generator.mutate(
                parent_strategy=parent_strategy,
                descriptor=descriptor,
                history=self.strategy_db.records,
            )
            if await self._validate_strategy(new_strategy):
                return new_strategy

        # Fallback to previous strategy if all mutations fail validation
        return self.current_strategy

    # ────────────────────────────────
    # Main loop
    # ────────────────────────────────

    def _random_seed_content(self) -> str:
        """Generate a small random candidate to seed the population."""
        chars = "abcdefghijklmnopqrstuvwxyz0123456789 +-*/()"
        length = self.rng.randint(1, 5)
        return "".join(self.rng.choice(chars) for _ in range(length))

    async def run(self) -> MetaEvolutionResult:
        """Run the full EvoX meta-evolution loop."""
        # Compile initial strategy eagerly to fail fast
        self.current_strategy.compile()

        # Seed population if empty with a few random candidates
        if not self.population:
            for _ in range(3):
                content = self._random_seed_content()
                score, artifacts = self.evaluator(content)
                objectives = artifacts.get("objectives", {}) if isinstance(artifacts, dict) else {}
                score = self._resolve_score(score, objectives)
                self.population.append(
                    Candidate(
                        content=content,
                        score=score,
                        artifacts=artifacts,
                        objectives=objectives,
                        generation=0,
                    )
                )

        window_start_score = max(c.score for c in self.population)
        window_start_iter = self.iteration
        last_best_score = window_start_score

        while self.iteration < self.config.total_iterations:
            await self._evolve_one()
            self.iteration += 1

            current_best = max(c.score for c in self.population)
            if current_best > last_best_score:
                last_best_score = current_best
                self.steps_since_improvement = 0
            else:
                self.steps_since_improvement += 1

            # Check if window has elapsed
            window_elapsed = self.iteration - window_start_iter
            if window_elapsed >= self.config.window_size:
                window_end_score = current_best
                score_signal = self._strategy_score(
                    window_start_score, window_end_score, window_elapsed
                )

                descriptor = PopulationDescriptor.from_population(
                    self.population,
                    recent_window=self.population[-window_elapsed:],
                    steps_since_improvement=self.steps_since_improvement,
                    selection_counts=self._selection_counts,
                )
                self.strategy_db.add(
                    StrategyRecord(
                        strategy=copy.deepcopy(self.current_strategy),
                        descriptor=descriptor.to_dict(),
                        score=score_signal,
                        window_size=window_elapsed,
                        start_score=window_start_score,
                        end_score=window_end_score,
                        deployed_at=self.iteration - window_elapsed,
                    )
                )

                delta = window_end_score - window_start_score
                if delta <= self.config.stagnation_threshold:
                    new_strategy = await self._meta_evolve_strategy()
                    if new_strategy.id != self.current_strategy.id:
                        self.current_strategy = new_strategy
                        self.strategy_switches += 1

                # Reset window
                window_start_score = current_best
                window_start_iter = self.iteration

        best = max(self.population, key=lambda c: c.score)
        return MetaEvolutionResult(
            best_candidate=best,
            population=self.population,
            strategy_history=list(self.strategy_db.records),
            iterations=self.iteration,
            strategy_switches=self.strategy_switches,
        )
