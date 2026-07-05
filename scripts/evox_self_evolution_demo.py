"""Demonstrate EvoX self-evolution on 3 real cases and report what evolved.

Usage:
    source .venv/bin/activate
    python scripts/evox_self_evolution_demo.py
"""

from __future__ import annotations

import asyncio
import json

from vibe.evox.circle_packing import CirclePackingMockGenerator, circle_packing_evaluator
from vibe.evox.evaluators import (
    expression_evaluator,
    keyword_coverage_evaluator,
    string_match_evaluator,
)
from vibe.evox.generators import MockSolutionGenerator, MockStrategyGenerator
from vibe.evox.loop import MetaEvolutionConfig, MetaEvolutionLoop


async def run_case(name: str, loop: MetaEvolutionLoop) -> dict:
    result = await loop.run()
    return {
        "name": name,
        "best_score": result.best_candidate.score,
        "best_candidate": result.best_candidate.content[:200],
        "iterations": result.iterations,
        "strategy_switches": result.strategy_switches,
        "strategies": [
            {
                "deployed_at": r.deployed_at,
                "window_size": r.window_size,
                "score_signal": r.score,
                "delta": r.end_score - r.start_score,
                "code": r.strategy.code,
            }
            for r in result.strategy_history
        ],
    }


async def main():
    cases = []

    # Case 1: String match — EvoX should move from random to greedy/diverse search.
    cases.append(
        await run_case(
            "string_match_target_hello",
            MetaEvolutionLoop(
                evaluator=string_match_evaluator("hello"),
                solution_generator=MockSolutionGenerator(seed=21),
                strategy_generator=MockStrategyGenerator(seed=21),
                config=MetaEvolutionConfig(
                    total_iterations=80,
                    window_size=12,
                    stagnation_threshold=0.5,
                    problem_description="Find a string close to 'hello'.",
                ),
                seed=21,
            ),
        )
    )

    # Case 2: Keyword coverage — EvoX should shift to strategies that preserve
    # and combine useful tokens.
    cases.append(
        await run_case(
            "keyword_coverage_fast_safe_simple",
            MetaEvolutionLoop(
                evaluator=keyword_coverage_evaluator(["fast", "safe", "simple"]),
                solution_generator=MockSolutionGenerator(seed=22),
                strategy_generator=MockStrategyGenerator(seed=22),
                config=MetaEvolutionConfig(
                    total_iterations=100,
                    window_size=15,
                    stagnation_threshold=1e-3,
                    problem_description="Generate text containing fast, safe, simple.",
                ),
                seed=22,
            ),
        )
    )

    # Case 3: Circle packing — EvoX should discover grid/hex structural patterns.
    cases.append(
        await run_case(
            "circle_packing_n12",
            MetaEvolutionLoop(
                evaluator=circle_packing_evaluator(n=12),
                solution_generator=CirclePackingMockGenerator(n=12, seed=23),
                strategy_generator=MockStrategyGenerator(seed=23),
                config=MetaEvolutionConfig(
                    total_iterations=100,
                    window_size=18,
                    stagnation_threshold=1e-4,
                    problem_description="Maximize non-overlapping circle density.",
                ),
                seed=23,
            ),
        )
    )

    for case in cases:
        print("=" * 70)
        print(f"CASE: {case['name']}")
        print(f"  iterations:        {case['iterations']}")
        print(f"  strategy switches: {case['strategy_switches']}")
        print(f"  best score:        {case['best_score']:.4f}")
        print(f"  best candidate:    {case['best_candidate']!r}")
        print()
        print("  Strategy evolution timeline:")
        for i, strat in enumerate(case["strategies"], 1):
            print(f"    [{i}] deployed_at={strat['deployed_at']} "
                  f"window={strat['window_size']} "
                  f"delta={strat['delta']:.4f} "
                  f"J(S)={strat['score_signal']:.4f}")
            for line in strat["code"].strip().splitlines():
                print(f"        {line}")
            print()

    # Save raw results for downstream analysis
    with open("evox_self_evolution_results.json", "w") as f:
        json.dump(cases, f, indent=2)
    print("Raw results saved to evox_self_evolution_results.json")


if __name__ == "__main__":
    asyncio.run(main())
