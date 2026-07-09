"""CLI commands for running EvoX meta-evolution."""

from __future__ import annotations

import asyncio

import typer

from vibe.evox.circle_packing import CirclePackingMockGenerator, circle_packing_evaluator
from vibe.evox.evaluators import (
    expression_evaluator,
    keyword_coverage_evaluator,
    string_match_evaluator,
    toy_signal_filter_evaluator,
)
from vibe.evox.generators import MockSolutionGenerator, MockStrategyGenerator
from vibe.evox.loop import MetaEvolutionConfig, MetaEvolutionLoop

evox_app = typer.Typer(help="Run EvoX meta-evolution experiments")


async def _run_experiment(evaluator_name: str, target: str, iterations: int, seed: int):
    if evaluator_name == "string":
        evaluator = string_match_evaluator(target)
        solution_generator = MockSolutionGenerator(seed=seed)
        problem = f"EvoX string-match experiment with target {target}"
    elif evaluator_name == "expression":
        evaluator = expression_evaluator(float(target))
        solution_generator = MockSolutionGenerator(seed=seed)
        problem = f"EvoX expression experiment with target {target}"
    elif evaluator_name == "keywords":
        evaluator = keyword_coverage_evaluator(target.split(","))
        solution_generator = MockSolutionGenerator(seed=seed)
        problem = f"EvoX keyword experiment with targets {target}"
    elif evaluator_name == "circle_packing":
        n = int(target) if target else 12
        evaluator = circle_packing_evaluator(n=n)
        solution_generator = CirclePackingMockGenerator(n=n, seed=seed)
        problem = "Maximize circle packing density in a unit square"
    elif evaluator_name == "signal_filter":
        evaluator = toy_signal_filter_evaluator()
        solution_generator = MockSolutionGenerator(seed=seed)
        problem = "Multi-objective signal filter: balance smoothness and responsiveness"
    else:
        raise typer.BadParameter(f"Unknown evaluator: {evaluator_name}")

    config = MetaEvolutionConfig(
        total_iterations=iterations,
        window_size=max(5, iterations // 10),
        stagnation_threshold=1e-6,
        problem_description=problem,
    )

    loop = MetaEvolutionLoop(
        evaluator=evaluator,
        solution_generator=solution_generator,
        strategy_generator=MockStrategyGenerator(seed=seed),
        config=config,
        seed=seed,
    )

    result = await loop.run()
    return result


@evox_app.command("run")
def evox_run(
    evaluator: str = typer.Option(
        "string",
        "--evaluator",
        "-e",
        help="Evaluator: string, expression, keywords, circle_packing, signal_filter",
    ),
    target: str = typer.Option(
        "hello world",
        "--target",
        "-t",
        help="Target string/value/keywords (or number of circles for circle_packing)",
    ),
    iterations: int = typer.Option(50, "--iterations", "-i", help="Total evaluation budget"),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed"),
):
    """Run an EvoX experiment with mock generators (no LLM required)."""
    result = asyncio.run(_run_experiment(evaluator, target, iterations, seed))
    print(f"Best score: {result.best_candidate.score:.4f}")
    print(f"Best candidate: {result.best_candidate.content[:200]!r}")
    print(f"Iterations: {result.iterations}")
    print(f"Strategy switches: {result.strategy_switches}")
    print(f"Final population size: {len(result.population)}")
