"""CLI commands for running EvoX meta-evolution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from vibe.evox.circle_packing import CirclePackingMockGenerator, circle_packing_evaluator
from vibe.evox.evaluators import (
    expression_evaluator,
    keyword_coverage_evaluator,
    string_match_evaluator,
    toy_signal_filter_evaluator,
)
from vibe.evox.generators import MockSolutionGenerator, MockStrategyGenerator
from vibe.evox.harness_target import (
    DEFAULT_EVAL_LIMIT,
    BuiltinEvalSuiteRunner,
    EvalSuiteError,
    HarnessEvaluator,
    HarnessSolutionGenerator,
    HarnessSpaceError,
    default_harness_space,
)
from vibe.evox.loop import MetaEvolutionConfig, MetaEvolutionLoop
from vibe.evox.tsp import TSPMockGenerator, tsp_evaluator
from vibe.evox.types import Candidate

evox_app = typer.Typer(help="Run EvoX meta-evolution experiments")


def _load_harness_config():
    """Load the live VibeConfig for harness runs (stubbed in tests)."""
    from vibe.core.config import VibeConfig

    return VibeConfig.load()


def _find_baseline_path() -> Path | None:
    """Locate docs/baseline_scorecard.json (repo checkout first, then cwd)."""
    repo_candidate = Path(__file__).resolve().parents[2] / "docs" / "baseline_scorecard.json"
    if repo_candidate.exists():
        return repo_candidate
    cwd_candidate = Path("docs") / "baseline_scorecard.json"
    return cwd_candidate if cwd_candidate.exists() else None


async def _run_harness(
    iterations: int, seed: int, limit: int, output_dir: str, working_dir: str
) -> None:
    """Run EvoX over the declared harness search space (bounded knob/prompt evolution)."""
    space = default_harness_space()
    config = _load_harness_config()
    output = Path(output_dir).expanduser()
    output.mkdir(parents=True, exist_ok=True)

    runner = BuiltinEvalSuiteRunner(
        base_config=config, working_dir=working_dir, reports_dir=output / "reports"
    )
    # Fail fast with a clean message when no model endpoint is reachable.
    await runner.probe()

    evaluator = HarnessEvaluator(
        space,
        runner,
        baseline_path=_find_baseline_path(),
        provenance_path=output / "harness_candidates.jsonl",
        eval_limit=limit,
    )

    # Reference: score the unmodified harness first. Anchors the improvement
    # check and surfaces eval infrastructure errors before the loop starts.
    reference_point = space.default_point()
    ref_score, ref_artifacts = evaluator.evaluate(reference_point)
    if "error" in ref_artifacts:
        raise EvalSuiteError(f"reference evaluation failed: {ref_artifacts['error']}")
    evaluator.reference_score = ref_score
    print(
        f"Reference score (unmodified harness): {ref_score:.2%} "
        f"(baseline gate: {evaluator.baseline_score:.2%}, limit: {limit} cases/candidate)"
    )

    loop = MetaEvolutionLoop(
        evaluator=evaluator,
        solution_generator=HarnessSolutionGenerator(space, seed=seed),
        strategy_generator=MockStrategyGenerator(seed=seed),
        config=MetaEvolutionConfig(
            total_iterations=iterations,
            window_size=max(3, iterations // 5),
            stagnation_threshold=1e-6,
            max_strategy_retries=1,
            validation_trials=1,
            problem_description=(
                "Optimize the agent harness (memory/reflection config knobs and "
                "extraction/reflection prompt variants) to maximize the built-in "
                "eval suite score."
            ),
        ),
        initial_candidates=[
            Candidate(
                content=space.encode(reference_point), score=ref_score, artifacts=ref_artifacts
            )
        ],
        seed=seed,
    )
    result = await loop.run()

    # If every loop candidate errored (e.g. the endpoint died mid-run), fail
    # cleanly instead of reporting a meaningless "best".
    evaluated = result.population[1:]  # exclude the seeded reference candidate
    errored = [c for c in evaluated if isinstance(c.artifacts, dict) and "error" in c.artifacts]
    if evaluated and len(errored) == len(evaluated):
        raise EvalSuiteError(
            f"all {len(errored)} candidate evaluations failed "
            f"(first error: {errored[0].artifacts['error']})"
        )

    best = result.best_candidate
    accepted = bool(isinstance(best.artifacts, dict) and best.artifacts.get("accepted"))
    print(f"Best score: {best.score:.4f} (reference: {ref_score:.4f})")
    print(f"Best candidate: {best.content[:200]}")
    print(f"Iterations: {result.iterations} | Strategy switches: {result.strategy_switches}")
    print(f"Provenance log: {evaluator.provenance_path}")

    if accepted:
        accepted_path = output / "accepted_overrides.json"
        overrides = space.to_overrides(space.decode(best.content))
        accepted_path.write_text(
            json.dumps(overrides, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"ACCEPTED — overrides written to {accepted_path}")
        print("Apply them to ~/.vibe/config.yaml to adopt this harness configuration.")
    else:
        reason = (
            best.artifacts.get("decision_reason", "gate rejected")
            if isinstance(best.artifacts, dict)
            else "gate rejected"
        )
        print(f"REJECTED by acceptance gate — {reason}. Config left unchanged.")


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
    elif evaluator_name == "tsp":
        n = int(target) if target else 10
        evaluator = tsp_evaluator(n=n, seed=seed)
        solution_generator = TSPMockGenerator(n=n, seed=seed)
        problem = f"Traveling Salesman Problem with {n} cities"
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
        help="Evaluator: string, expression, keywords, circle_packing, signal_filter, tsp",
    ),
    target: str = typer.Option(
        "hello world",
        "--target",
        "-t",
        help="Target string/value/keywords, 'harness' for harness evolution "
        "(or number of circles for circle_packing)",
    ),
    iterations: int = typer.Option(50, "--iterations", "-i", help="Total evaluation budget"),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed"),
    limit: int = typer.Option(
        DEFAULT_EVAL_LIMIT,
        "--limit",
        "-n",
        help="Eval cases per candidate (--target harness only; mirrors CI's --limit 20)",
    ),
    output_dir: str = typer.Option(
        "~/.vibe/evox/harness",
        "--output-dir",
        "-o",
        help="Provenance/report output dir (--target harness only)",
    ),
    working_dir: str = typer.Option(
        ".", "--working-dir", "-w", help="Working dir for harness eval runs"
    ),
):
    """Run an EvoX experiment (mock generators) or harness evolution (--target harness)."""
    if target == "harness":
        try:
            asyncio.run(_run_harness(iterations, seed, limit, output_dir, working_dir))
        except (EvalSuiteError, HarnessSpaceError) as e:
            typer.secho(f"evox harness: {e}", err=True)
            raise typer.Exit(code=1)
        except Exception as e:
            # Never dump stack traces for operational failures.
            typer.secho(f"evox harness: evolution failed: {e}", err=True)
            raise typer.Exit(code=1)
        return

    result = asyncio.run(_run_experiment(evaluator, target, iterations, seed))
    print(f"Best score: {result.best_candidate.score:.4f}")
    print(f"Best candidate: {result.best_candidate.content[:200]!r}")
    print(f"Iterations: {result.iterations}")
    print(f"Strategy switches: {result.strategy_switches}")
    print(f"Final population size: {len(result.population)}")
