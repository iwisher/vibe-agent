# EvoX Implementation Notes

This document describes the actual EvoX meta-evolution algorithm implementation added to Vibe Agent for the goal: *implement the EvoX paper (arXiv:2602.23413v1) into the project*.

## 1. Scope

The implementation is a **full realization** of the two-level evolution process from the paper:

- **Inner loop**: evolves candidate solutions under a search strategy.
- **Outer loop**: meta-evolves the search strategy itself — represented as executable Python code — when progress stagnates.

It is delivered as a new `vibe.evox` package, a `vibe evox run` CLI command, and a test suite with four validation use cases plus baseline-comparison tests.

## 2. Files Added

```
vibe/evox/
├── __init__.py        # Public API exports
├── types.py           # Candidate (now with objectives), VariationOperator, Evaluator protocol
├── strategy_code.py   # EvolvableStrategy: compile + execute Python strategy code
├── strategy.py        # SearchStrategy, StrategyRecord, StrategyDatabase
├── population.py      # PopulationDescriptor φ(D_t) with objective stats
├── generators.py      # SolutionGenerator + StrategyGenerator (LLM + mock)
├── loop.py            # MetaEvolutionLoop (core algorithm)
├── metrics.py         # Multi-objective proxy scoring (AdaEvolve evaluation logic)
├── evaluators.py      # Toy evaluators (string, expression, keywords, signal filter)
├── circle_packing.py  # Paper-inspired benchmark evaluator + domain generator
├── tsp.py             # Traveling Salesman Problem evaluator + domain-aware generator
└── cli.py             # `vibe evox run` command

tests/evox/
└── test_evox.py       # Unit tests + use-case tests + baseline + multi-objective + UCB tests
```

**Files modified:**
- `vibe/cli/main.py` — registered the new `vibe evox` Typer subcommand.
- `vibe/dashboard/server.py` — added `/api/research/papers` REST endpoints.
- `vibe/dashboard/static/app.js` — added React `ResearchPaperPage` for EvoX.
- `vibe/dashboard/static/style.css` — added dark-themed research paper styles.
- `tests/test_dashboard_api.py` — added coverage for research paper endpoints.

**Files added (documentation & demo):**
- `docs/EvoX_implementation.md` — this document.
- `scripts/evox_self_evolution_demo.py` — runnable 3-case self-evolution demo.

## 3. Core Data Model

### Candidate
A scored solution:

```python
@dataclass
class Candidate:
    content: str
    score: float
    artifacts: dict[str, Any]
    generation: int
    parent_id: str | None
    strategy_id: str | None
    operator: VariationOperator | None
```

### EvolvableStrategy / SearchStrategy
A search strategy is now **executable Python code** rather than a static config. The code must define three functions:

```python
def select_parent(population, rng) -> Candidate: ...
def select_inspiration(population, parent, rng) -> list[str]: ...
def select_operator(rng) -> VariationOperator | str: ...
```

`EvolvableStrategy` compiles the source with a restricted import allowlist (`random`, `math`) and validates that the required functions exist. `SearchStrategy` is a higher-level wrapper that can synthesize this code from legacy configuration fields or accept code directly. The meta-generator now mutates the Python source itself.

### PopulationDescriptor
`φ(D_t)` summarizes the current state of the solution population:

- Score statistics: best, median, mean, std, p25, p75
- Frontier: top-k scores
- Progress indicators: steps since last improvement
- Recent-window statistics: mean score and improvement over the last `W` steps
- Diversity proxy: mean pairwise content-length difference
- Overuse patterns: per-candidate selection counts, max selection ratio, and overused candidate ids

### StrategyDatabase
Memory of previously deployed strategies. Each record stores:

- The strategy itself
- The population descriptor at deployment time
- The observed score signal `J(S)`
- Window start/end scores and size

## 4. Algorithm

The `MetaEvolutionLoop.run()` method implements Algorithm 1 from the paper:

### 4.1 Initialization
- Seed the population with a few random candidates if none are provided.
- Start with a simple `uniform_random` / `free_form` strategy.
- Eagerly compile the initial strategy so invalid code fails fast.

### 4.2 Solution Evolution (inner loop)
For each iteration:

1. Compile and call the active strategy's `select_parent` to choose a parent.
2. Track the parent selection in `_selection_counts`.
3. Call `select_operator` to sample a variation operator.
4. Call `select_inspiration` to build an inspiration set.
5. Call the `SolutionGenerator` to produce a new candidate.
6. Evaluate the candidate with the user-provided `Evaluator`.
7. Append the candidate to the population.

### 4.3 Progress Monitoring
After every window of `W` evaluations:

- Compute `Δ = s_end − s_start`.
- Compute the strategy signal:

```
J(S) = (s_end − s_start) * log(1 + s_start) / sqrt(W)
```

- Record the deployed strategy in the `StrategyDatabase`.
- If `Δ <= stagnation_threshold`, trigger meta-evolution.

### 4.4 Meta-Evolution (outer loop)
When stagnation is detected:

1. Build the current `PopulationDescriptor`, including `_selection_counts`.
2. Select a parent strategy from the strategy database using score-biased selection.
3. Call the `StrategyGenerator` to mutate the parent strategy conditioned on `φ(D_t)` and history. The LLM generator edits Python source; the mock generator rotates predefined strategies.
4. Validate the new strategy by compiling it and running a few trial generations (without consuming the main budget).
5. If validation passes, deploy the new strategy; otherwise retry up to `max_strategy_retries` and fall back to the previous strategy.
6. The solution population is **never reset** across strategy switches.

## 5. Generators

### LLM-backed generators
- `LLMSolutionGenerator` — builds a natural-language prompt from the parent, operator, inspiration set, and problem description, then calls `LLMClient.complete()`.
- `LLMStrategyGenerator` — prompts an LLM to output a new Python strategy code block, conditioned on the population descriptor and recent strategy history. It eagerly compiles the returned code to reject invalid responses.

### Mock generators
- `MockSolutionGenerator` — deterministic, no-LLM generator that performs random character-level edits for fast testing.
- `MockStrategyGenerator` — rotates through a small set of predefined strategies to demonstrate adaptation without an LLM.
- `CirclePackingMockGenerator` — domain-aware generator that produces JSON circle lists and supports grid/hex structural variations.

The CLI, validation tests, and self-evolution demo use the mock generators by default so they run quickly and deterministically.

## 6. Usage

### CLI

```bash
# String-match evolution
python -m vibe evox run --evaluator string --target "hello" --iterations 50

# Arithmetic expression discovery
python -m vibe evox run --evaluator expression --target "42" --iterations 80

# Keyword coverage
python -m vibe evox run --evaluator keywords --target "fast,safe,simple" --iterations 100

# Circle packing (target = number of circles)
python -m vibe evox run --evaluator circle_packing --target 12 --iterations 80

# Traveling Salesman Problem (target = number of cities)
python -m vibe evox run --evaluator tsp --target 10 --iterations 60
```

### Self-evolution demo

```bash
python scripts/evox_self_evolution_demo.py
```

The demo runs three real self-evolution cases and writes `evox_self_evolution_results.json`:

1. **String match target "hello"** — demonstrates strategy cycling when stuck at a local optimum.
2. **Keyword coverage "fast,safe,simple"** — shows repeated stagnation due to a generator expressiveness ceiling.
3. **Circle packing n=12** — shows a big jump in packing density after meta-evolution switches to structural variation and discovers a grid layout.

The saved JSON records every deployed strategy, its `window_size`, `score_signal`, `delta`, and the full evolved Python source code, making the evolution trajectory inspectable.

### Dashboard research page

Open the dashboard and navigate to the EvoX paper page to view:

- Paper metadata (authors, affiliations, venue, URL)
- Tabbed sections: Overview, Problem, Method, Results
- Live data served by `/api/research/papers` and `/api/research/papers/{id}`

### Library API

```python
import asyncio
from vibe.evox import MetaEvolutionLoop, MetaEvolutionConfig
from vibe.evox.evaluators import string_match_evaluator
from vibe.evox.generators import MockSolutionGenerator, MockStrategyGenerator

async def main():
    loop = MetaEvolutionLoop(
        evaluator=string_match_evaluator("hello"),
        solution_generator=MockSolutionGenerator(seed=1),
        strategy_generator=MockStrategyGenerator(seed=1),
        config=MetaEvolutionConfig(total_iterations=60, window_size=10),
        seed=1,
    )
    result = await loop.run()
    print(result.best_candidate.content, result.best_candidate.score)

asyncio.run(main())
```

## 7. Validation

Use-case tests in `tests/evox/test_evox.py`:

1. **String match (UC1)** — evolves toward a target string. Asserts improvement over the random seed baseline and correct iteration/population counts.
2. **Expression discovery (UC2)** — evolves arithmetic expressions toward a target value. Asserts finite scores and non-empty strategy history.
3. **Keyword coverage (UC3)** — evolves content to cover required keywords. Asserts score in `[0, 1]` and recorded strategy history.
4. **Circle packing (UC4)** — paper-inspired benchmark. Asserts valid non-overlapping circles and a density in `[0, 1]`.

Baseline-comparison tests (`TestEvoXBaselines`):

- Run EvoX with a window size larger than the budget so meta-evolution never triggers. This acts as a fixed-strategy baseline.
- Run EvoX with normal settings so meta-evolution triggers.
- Assert that the adaptive run switches strategy at least once and achieves a score **greater than or equal to** the fixed-strategy baseline on the same budget.
- Tests cover both string match and circle packing.

**Self-evolution demo results:**

Running `python scripts/evox_self_evolution_demo.py` produced:

| Case | Best score | Iterations | Strategy switches | Key evolved behavior |
|---|---|---|---|---|
| String match "hello" | -3 (candidate "0eo") | 80 | 4 | uniform_random/free_form → best/local_refinement → diverse/structural_variation → ucb/frontier/free_form |
| Keyword coverage | 0.0 | 100 | 4 | Cycled through strategies but could not overcome the 2-char mock generator ceiling |
| Circle packing n=12 | 0.368 | 100 | 2 | Random/free_form hit a plateau; diverse/structural_variation discovered a grid layout and boosted density by ~0.197 |

**Test results:**

```
tests/evox/test_evox.py         10 passed
tests/test_dashboard_api.py    22 passed
tests/dashboard/test_api.py    13 passed
tests/cli/test_memory_commands.py 3 passed
```

## 8. Integration with Existing Vibe Agent Code

- The EvoX package is **additive**: it does not modify `QueryLoop`, `EvalRunner`, `ModelGateway`, or other core components.
- `vibe/cli/main.py` was updated to register the new `vibe evox` subcommand.
- `vibe/dashboard/server.py`, `app.js`, `style.css`, and `tests/test_dashboard_api.py` were extended to add the EvoX research paper page.
- `LLMSolutionGenerator` and `LLMStrategyGenerator` reuse the existing `vibe.core.model_gateway.LLMClient` for real LLM calls.
- The package can be used standalone or wrapped into an eval case in the future.

## 9. Commits

The implementation was committed and pushed to `main` as four focused commits:

1. `3690cf3` — `feat(evox): implement core meta-evolution library with executable strategies`
2. `09dd383` — `feat(cli): add EvoX subcommand integration`
3. `36ee3a6` — `feat(dashboard): add research paper page for EvoX`
4. `7c1c8b0` — `docs: add EvoX implementation guide and self-evolution demo`

## 10. What I Learned from the Paper's Signal-Processing Case Study

The paper's signal-processing case study (Section 5, Figure 2) shows how EvoX evolves its search strategy through **four phases** under a 100-iteration budget. The task is to build a filtering program for a noisy, changing time series, balancing four competing objectives: fidelity, smoothness, lag, and false trend changes.

### Phase 1: Random Search and Greedy Search

- **Behavior**: EvoX starts with the same uniform-random strategy as the static baseline (random parent, random inspiration, free-form variation).
- **Stagnation**: Around iteration 20, progress stops.
- **Response**: Switch to a **greedy strategy** that refines only the single best program found so far.
- **Why it fails**: The current best program still relies on simple moving-average (MA) or exponential-moving-average (EMA) structures. Tiny local refinements cannot escape that structural ceiling.
- **Lesson**: Greedy exploitation is ineffective when the current best has the wrong structure.

### Phase 2: The Breakthrough (Stratified + Multi-Objective)

- **Trigger**: Around iteration 40, after the greedy strategy also stagnates.
- **Key insight**: Ranking by a single combined score can hide complementary candidates. Two "mediocre" average scores may come from opposite strengths (e.g., one very smooth but high-lag, another responsive but noisy).
- **Strategy**: Stop selecting purely by combined score. Instead, sample parents and inspirations from **diverse score tiers and objective-specific groups**, then pair a parent strong on one objective with an inspiration strong on a complementary objective.
- **Result**: The LLM is prompted to merge complementary strengths, leading to novel hybrid designs such as **singular spectrum analysis (SSA) combined with Whittaker smoothing** — the largest single performance jump.
- **Lesson**: Multi-objective stratification can recombine partial strengths that single-score ranking would discard.

### Phase 3: Structural Exploration (UCB + Structural Variation)

- **Trigger**: Around iteration 60, the population descriptor shows many recent candidates with similar scores.
- **Diagnosis**: Simple refinements and combinations are no longer producing novelty.
- **Strategy**: Increase use of **structural variation** for bold redesigns, and apply a **UCB selection rule** that prioritizes rarely selected parents (encouraging exploration). Multi-objective inspiration selection is retained because it proved useful.
- **Result**: New solutions begin using advanced SciPy building blocks — higher-order filters, smoothing kernels, and forward–backward filtering (`filtfilt`).
- **Lesson**: When the population clusters around similar scores, the right response is exploratory structural variation plus under-sampled parent selection (UCB), not more refinement.

### Phase 4: Final Polishing (UCB + Local Refinement)

- **Trigger**: Around iteration 90, large structural changes start destabilizing performance.
- **Diagnosis**: The search is near the frontier; big moves are more likely to hurt than help.
- **Strategy**: Shift back to **local refinement** on the top discovered solutions, while keeping UCB parent selection to avoid premature convergence.
- **Result**: Small, precise adjustments yield final incremental gains (+0.022).
- **Lesson**: Late-stage optimization should favor refinement over structural variation, but still maintain some exploration pressure to avoid getting trapped.

### Mapping the four phases to strategy knobs

| Phase | Parent selection | Inspiration selection | Operator | Purpose |
|---|---|---|---|---|
| 1a | uniform_random | none / uniform_random | free_form | Baseline exploration |
| 1b | best | none | local_refinement | Greedy exploitation (fails) |
| 2 | diverse / objective-stratified | objective-complementary | free_form / combine | Breakthrough hybrids |
| 3 | UCB (rarely selected) | multi-objective / diverse | structural_variation | Explore new families |
| 4 | UCB | top / frontier | local_refinement | Final polishing |

### What is now implemented

The latest update added the AdaEvolve-style multi-objective evaluation layer:

- **`vibe/evox/metrics.py`**: `compute_proxy_score()` mirrors SkyDiscover's `_get_progress_score()`. It supports a `fitness_key`, a list of `pareto_objectives`, and per-objective `higher_is_better` directions.
- **`Candidate.objectives`**: every candidate now carries a `dict[str, float]` of per-objective scores in addition to its scalar `score`.
- **`MetaEvolutionConfig.pareto_objectives` / `higher_is_better` / `fitness_key`**: configure multi-objective mode; when set, the loop replaces the evaluator's raw score with the proxy.
- **`PopulationDescriptor.objective_stats`**: aggregates best/worst/mean/count per objective.
- **Real UCB parent selection**: `SearchStrategy` synthesizes code that uses `context["selection_counts"]` and the UCB1 formula (`score + sqrt(2*ln(total)/(count+1))`).
- **LLM prompt guidance**: `LLMStrategyGenerator` now includes task-objective text and a diversity note, and explicitly tells the LLM to prefer parents/inspirations with complementary objective strengths when `pareto_objectives` are present.
- **Toy signal-filter evaluator**: `toy_signal_filter_evaluator()` demonstrates multi-objective evaluation with competing `smoothness` and `responsiveness` objectives.
- **TSP evaluator**: `tsp_evaluator()` and `TSPMockGenerator` demonstrate a representative complex case — structured JSON candidates, hard permutation constraints, and domain-aware 2-opt / order-crossover variation.
- **Guardrail tests**: 28 tests now cover metrics, UCB selection, multi-objective loop behavior, sandbox escapes, and TSP optimization.

### What is still missing

- **Objective-aware inspiration pairing**: the loop tracks per-objective stats but does not yet select an inspiration specifically to complement the parent's weakest objective.
- **Domain-specific structural variation prompts**: e.g., telling the generator to use SciPy filters or merge SSA with Whittaker smoothing.
- **The signal-processing benchmark itself**: the four-phase trajectory was observed on that specific task; only a toy two-objective evaluator exists.
- **Guide-LLM summaries**: SkyDiscover uses a cheaper guide model to summarize population stats and reference algorithms; my implementation passes raw descriptor JSON to the strategy LLM.

### Validation against reference implementations

I inspected the SkyDiscover reference implementations:

- [`skydiscover/context_builder/evox/builder.py`](https://github.com/skydiscover-ai/skydiscover/blob/main/skydiscover/context_builder/evox/builder.py) — context builder for discovering search algorithms
- [`skydiscover/context_builder/evox/formatters.py`](https://github.com/skydiscover-ai/skydiscover/blob/main/skydiscover/context_builder/evox/formatters.py) — population/state formatting
- [`skydiscover/context_builder/adaevolve/builder.py`](https://github.com/skydiscover-ai/skydiscover/blob/main/skydiscover/context_builder/adaevolve/builder.py) — adaptive evolution context builder with multi-objective support

What these implementations confirm about my understanding:

1. **Search strategy as an evolvable program**: Their `EvoxContextBuilder` treats the search algorithm itself as a `Program` object with a `solution` field containing the strategy code, plus a `combined_score` measuring how much it improved downstream solutions. This matches my design of executable `EvolvableStrategy` / `SearchStrategy` code.

2. **Window-based scoring is central**: They track `search_window_start_score`, `search_window_end_score`, `window_start_iteration`, and `search_window_horizon` for every deployed algorithm. This aligns with my `MetaEvolutionLoop` window logic and `J(S) = Δ * log(1+s_start) / sqrt(W)` signal.

3. **Multi-objective mode exists and uses a scalar proxy**: `AdaEvolveContextBuilder._is_multiobjective_enabled()` checks `pareto_objectives`, and `_get_progress_score()` collapses multiple objectives into a single proxy for progress descriptions. This confirms that Phase 2's objective-aware pairing requires per-objective metrics and a configured scalarization.

4. **Prompt engineering is extensive**: Their context builder makes parallel calls to a cheaper "guide" LLM to generate population-statistics insights, problem-context summaries, and batch summaries of reference algorithms. It then assembles everything through templates (`search_evolution_user_message.txt`, `system_message.txt`, etc.). My implementation has minimal prompt templating in `generators.py` and does not use a guide LLM for summaries.

5. **Stagnation response includes paradigm guidance**: `AdaEvolveContextBuilder._format_paradigm_guidance()` injects a "BREAKTHROUGH IDEA" block when the search is globally stagnating, with concrete implementation instructions and cautions. My implementation detects stagnation but does not inject paradigm-level guidance into prompts.

6. **Sibling context prevents repeated failures**: They track previous mutations of the same parent (`siblings`) and summarize improved/regressed/unchanged counts. My implementation records parent IDs but does not build sibling history or explicitly tell the generator to avoid failed approaches.

7. **Population state formatting is much richer**: `format_population_state()` and `format_db_stats_diff()` report score tiers, reuse rates, iterations without improvement, execution traces, and SOTA gaps. My `PopulationDescriptor` is simpler (best/median/std, diversity proxy, overuse ids).

### Critique of my implementation

- **Correct core abstraction**: The two-level loop, executable strategies, window-based signal, and strategy database are all sound and match the reference architecture.
- **Multi-objective plumbing is now present**: `pareto_objectives`, `higher_is_better`, scalar proxy scoring, and objective stats in the descriptor are implemented. Objective-aware inspiration pairing is still a gap.
- **UCB selection is now real**: selection counts are tracked and passed to strategy code; the synthesized UCB branch uses the UCB1 formula.
- **Prompts are still under-engineered**: The reference uses a dedicated context builder + guide LLM + templates. My `LLMStrategyGenerator` builds a single inline prompt with multi-objective hints. This is better than before but still simpler than the reference.
- **No paradigm/sibling mechanisms**: These adaptive features make the search more robust; my code lacks them.
- **Benchmark coverage is thin**: The reference supports many domains via evaluator configs; I only have toy evaluators plus circle packing.

### Future tasks

1. **Register EvoX as the agent's offline process pipeline**: Currently `vibe evox run` is a standalone CLI command. The next step is to make EvoX a first-class offline pipeline stage inside Vibe Agent, so it can be invoked by the harness/eval system (e.g., `vibe pipeline evox --task <config>` or as an eval case type). This would allow EvoX to run against real agent tasks, persist populations and strategy databases to the trace store, and feed discovered strategies back into the main query loop.

## 11. Design Decisions & Limitations

- **Evolvable code, not just config**: The biggest departure from the initial minimal implementation is that strategies are now Python source code edited by the meta-generator. This matches the paper's `EvolvedProgramDatabase` concept.
- **Restricted execution environment**: Strategy code may only import `random` and `math`, `from ... import` is disallowed, and builtins are restricted to a small allowlist. This is a lightweight sandbox that blocks obvious escapes, but it is not a full isolation boundary (see limitations below).
- **Mock-first validation**: Tests use mock generators so they are fast and deterministic. LLM-backed generators are available for real experiments.
- **String-based candidates**: The current `Candidate.content` is a string, which covers prompts, programs, JSON circle lists, and symbolic expressions. Structured candidates (e.g., ASTs) would require extending `Candidate`.
- **Multi-objective scaffolding present, objective-aware pairing missing**: The loop can compute a scalar proxy from `pareto_objectives`, but it does not yet pair a parent strong on one objective with an inspiration strong on a complementary objective (Phase 2 of the signal-processing case study).
- **Limited complex benchmarks**: TSP demonstrates structured candidates and domain-aware variation, but the paper's full suite (Heilbronn triangle, MinMaxMinDist, PRISM, Cloudcast, Frontier-CS, ARC-AGI-2) is not implemented.
- **Lightweight sandbox, not full isolation**: The strategy-code sandbox restricts imports and builtins, but because it runs `exec()` in the main process, sophisticated Python object-introspection escapes are still theoretically possible. True isolation would require a subprocess or RestrictedPython.
- **No checkpointing**: The loop runs in memory. Persistence can be added by serializing the population and strategy database.
- **No full benchmark suite**: Only one paper-inspired domain (circle packing) is included. The math, systems, Frontier-CS, and ARC-AGI-2 benchmarks from the paper are out of scope.

## 12. References

- Shu Liu et al., *EvoX: Meta-Evolution for Automated Discovery*, arXiv:2602.23413v1 [cs.LG], 26 Feb 2026.
