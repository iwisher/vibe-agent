"""EvoX harness target — bounded knob/prompt evolution over the agent harness (D1 MVP).

Scope cut is deliberate: this is NOT arbitrary harness-code search (full
Meta-Harness-style code evolution is a much larger build). Instead, a declared,
data-driven search space of (a) memory/reflection config knobs and (b) named
prompt-template variants for extraction/reflection is evolved by the existing
MetaEvolutionLoop.

Scoring: each candidate's config overrides are applied to a fresh VibeConfig
and the built-in eval suite is run (default limit 20, mirroring CI's
``vibe eval run --limit 20``); the aggregate pass rate is the score.

Acceptance (arXiv 2605.30621 — harness updates ≠ benefit): a candidate is
accepted only if it improves over the reference (unmodified harness) score AND
shows no >5% drop vs ``docs/baseline_scorecard.json`` — the same convention as
``scripts/ci_eval_report.py``.

Provenance (Meta-Harness, arXiv 2603.28052 — the proposer needs full access to
prior candidates' scores/traces): every evaluation appends a JSONL record with
the candidate's overrides, score, acceptance decision, and eval-report path.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from vibe.evox.types import VariationOperator

logger = logging.getLogger(__name__)

# Regression-gate convention shared with scripts/ci_eval_report.py (5% drop).
DEFAULT_MAX_DROP_PCT = 0.05
DEFAULT_EVAL_LIMIT = 20


class HarnessSpaceError(ValueError):
    """Raised when a candidate encoding or space definition is invalid."""


class EvalSuiteError(RuntimeError):
    """Raised when the eval suite cannot run (endpoint unreachable, no cases...)."""


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------


def passes_regression_gate(
    current_score: float, baseline_score: float, max_drop_pct: float = DEFAULT_MAX_DROP_PCT
) -> bool:
    """Return True when ``current_score`` is within the allowed drop of baseline.

    Same convention as scripts/ci_eval_report.py (``current >= baseline * 0.95``),
    factored here so the CI script can share it later.
    """
    return current_score >= baseline_score * (1.0 - max_drop_pct)


def load_baseline_score(baseline_path: str | Path | None) -> float:
    """Load ``overall_score`` from the baseline scorecard; 1.0 on any failure."""
    if baseline_path is None:
        return 1.0
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        return float(data.get("overall_score", 1.0))
    except Exception as e:
        logger.warning(
            "Could not read baseline scorecard %s (%s); defaulting to 1.0", baseline_path, e
        )
        return 1.0


@dataclass(frozen=True)
class AcceptanceDecision:
    """Outcome of the held-out acceptance gate for one candidate."""

    accepted: bool
    improved: bool | None  # None when no reference score is available yet
    within_regression_bound: bool
    reason: str


def evaluate_acceptance(
    candidate_score: float,
    reference_score: float | None,
    baseline_score: float,
    max_drop_pct: float = DEFAULT_MAX_DROP_PCT,
) -> AcceptanceDecision:
    """Accept a candidate only if it improves AND stays within the regression bound."""
    within = passes_regression_gate(candidate_score, baseline_score, max_drop_pct)
    if reference_score is None:
        return AcceptanceDecision(
            accepted=False,
            improved=None,
            within_regression_bound=within,
            reason="no reference score yet (baseline check only)",
        )
    improved = candidate_score > reference_score
    if not improved:
        return AcceptanceDecision(
            accepted=False,
            improved=False,
            within_regression_bound=within,
            reason=(
                f"no improvement over reference ({candidate_score:.4f} <= {reference_score:.4f})"
            ),
        )
    if not within:
        return AcceptanceDecision(
            accepted=False,
            improved=True,
            within_regression_bound=False,
            reason=(
                f"regression vs baseline beyond {max_drop_pct:.0%} "
                f"({candidate_score:.4f} < {baseline_score * (1.0 - max_drop_pct):.4f})"
            ),
        )
    return AcceptanceDecision(
        accepted=True,
        improved=True,
        within_regression_bound=True,
        reason="improved over reference and within regression bound",
    )


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnobDimension:
    """One tunable config knob: dotted config path + candidate values."""

    path: str  # e.g. "memory.reflection.max_lessons"
    values: tuple[Any, ...]
    default_index: int = 0

    @property
    def default_value(self) -> Any:
        return self.values[self.default_index]


@dataclass(frozen=True)
class PromptSlot:
    """One overridable prompt template: named variants mapped to a config path.

    The variant named by ``default`` must exist; a variant mapped to None means
    "no override" (the built-in template stays in effect).
    """

    key: str  # e.g. "extraction"
    config_path: str  # e.g. "memory.wiki.extraction_prompt"
    variants: dict[str, str | None]
    default: str = "default"


# Conservative prompt variants for the default space. They keep every
# placeholder of the built-in templates ({transcript} for extraction;
# {max_lessons}/{outcome}/{query}/{pivotal_block}/{transcript} for reflection)
# and only change emphasis/formatting strictness — no structural prompt changes.

_EXTRACTION_VARIANT_STRICT_JSON = """You are a knowledge extraction engine. Analyze the \
following conversation and extract factual knowledge that should be preserved in a long-term wiki.

Instructions:
- Extract only concrete, factual information (not opinions, greetings, or small talk)
- Each item should be a self-contained knowledge nugget; prefer fewer, higher-quality items
- Use [[slug]] syntax to reference related concepts (e.g., [[python]], [[docker]])
- Include specific details: names, dates, versions, commands, URLs, decisions
- Tool messages appear as compact `[i] tool <name>: <output>` summaries — use \
them to learn what the agent actually did
- Also extract LESSON entries: reusable rules learned from what worked and what \
failed in this conversation, and why. A lesson must include the tag "lesson" in \
addition to its topic tags.

For each knowledge item, provide:
- title: A concise, descriptive title (3-8 words)
- content: The knowledge content in markdown format (2-5 sentences)
- tags: 2-5 relevant tags as a list of strings
- citations: Source references as list of dicts with keys "session" and "message_index"

Respond with ONLY a valid JSON array. No markdown code fences, no extra text, \
no trailing commentary. If nothing is worth preserving, respond with an empty array.

CONVERSATION:
{transcript}
"""

_EXTRACTION_VARIANT_DETAILED_CITATIONS = """You are a knowledge extraction engine. Analyze the \
following conversation and extract factual knowledge that should be preserved in a long-term wiki.

Instructions:
- Extract only concrete, factual information (not opinions, greetings, or small talk)
- Each item should be a self-contained knowledge nugget
- Use [[slug]] syntax to reference related concepts (e.g., [[python]], [[docker]])
- Include specific details: names, dates, versions, commands, URLs, decisions
- Tool messages appear as compact `[i] tool <name>: <output>` summaries — use \
them to learn what the agent actually did
- Every item MUST carry precise citations pointing at the session and message \
index it was learned from; never emit an item without citations
- Also extract LESSON entries: reusable rules learned from what worked and what \
failed in this conversation, and why. A lesson must include the tag "lesson" in \
addition to its topic tags.

For each knowledge item, provide:
- title: A concise, descriptive title (3-8 words)
- content: The knowledge content in markdown format (2-5 sentences)
- tags: 2-5 relevant tags as a list of strings
- citations: Source references as list of dicts with keys "session" and "message_index"

Respond with ONLY a JSON array. No markdown code fences, no extra text.

CONVERSATION:
{transcript}
"""

_REFLECTION_VARIANT_FAILURE_FIRST = """You are a trajectory reflection engine. Analyze the \
agent session below and distill up to {max_lessons} reusable lessons.

Session outcome: {outcome}
Original user query: {query}{pivotal_block}

Rules:
- Start from what went wrong: failures, corrections, retries, and dead ends are \
the primary signal; only then consider what worked.
- Each lesson must be a specific, reusable rule of the form "When X, do Y \
because Z" — never a restatement of what the task was.
- Tool messages appear as compact `[i] tool <name>: <output>` summaries — \
use them to learn what the agent actually did.
- kind must be one of: "pitfall" (something to avoid), "procedure" (a \
reusable routine that worked), "tip" (a generalizable insight).
- generality must be an integer 1-5 rating how reusable the lesson is beyond \
this specific task: 1 = tied to this specific instance, 5 = reusable \
principle.
- Return at most {max_lessons} lessons. If nothing is generalizable, return an \
empty JSON array.
- Respond with ONLY a JSON array of objects with keys "title", "lesson", \
"applies_when", "kind", "generality". No markdown code fences, no extra text.

TRAJECTORY:
{transcript}
"""

_REFLECTION_VARIANT_CONCISE = """You are a trajectory reflection engine. Analyze the \
agent session below and distill up to {max_lessons} reusable lessons.

Session outcome: {outcome}
Original user query: {query}{pivotal_block}

Rules:
- Each lesson must be a specific, reusable rule of the form "When X, do Y \
because Z" — never a restatement of what the task was.
- Keep each lesson to one or two sentences; prefer precision over coverage.
- Only distill lessons you would rate generality 3 or higher; marginal lessons \
must be omitted entirely.
- Failures and corrections are the richest signal: prefer lessons learned \
from errors, retries, and dead ends.
- Tool messages appear as compact `[i] tool <name>: <output>` summaries — \
use them to learn what the agent actually did.
- kind must be one of: "pitfall" (something to avoid), "procedure" (a \
reusable routine that worked), "tip" (a generalizable insight).
- generality must be an integer 1-5 rating how reusable the lesson is beyond \
this specific task: 1 = tied to this specific instance, 5 = reusable \
principle.
- Return at most {max_lessons} lessons. If nothing is generalizable, return an \
empty JSON array.
- Respond with ONLY a JSON array of objects with keys "title", "lesson", \
"applies_when", "kind", "generality". No markdown code fences, no extra text.

TRAJECTORY:
{transcript}
"""


class HarnessSearchSpace:
    """Declared, data-driven space of candidate harness mutations.

    A *point* in the space is ``{"knobs": {config_path: value}, "prompts":
    {slot_key: variant_name}}``. Points are encoded as canonical JSON strings
    so they can flow through the EvoX loop as ordinary candidate content.
    Extend the space by passing different ``knobs``/``prompt_slots`` (or via
    ``from_dict``) — nothing about the dimensions is hardcoded elsewhere.
    """

    def __init__(self, knobs: list[KnobDimension], prompt_slots: list[PromptSlot]) -> None:
        if not knobs and not prompt_slots:
            raise HarnessSpaceError("search space must declare at least one dimension")
        for knob in knobs:
            if not knob.values:
                raise HarnessSpaceError(f"knob {knob.path!r} has no candidate values")
            if not 0 <= knob.default_index < len(knob.values):
                raise HarnessSpaceError(f"knob {knob.path!r} default_index out of range")
        for slot in prompt_slots:
            if not slot.variants:
                raise HarnessSpaceError(f"prompt slot {slot.key!r} has no variants")
            if slot.default not in slot.variants:
                raise HarnessSpaceError(f"prompt slot {slot.key!r} default variant missing")
        self.knobs = list(knobs)
        self.prompt_slots = list(prompt_slots)
        self._knob_by_path = {k.path: k for k in self.knobs}
        self._slot_by_key = {s.key: s for s in self.prompt_slots}

    # ── points ──────────────────────────────────────────────────────────

    def default_point(self) -> dict[str, Any]:
        """The point reproducing the shipped configuration (no-op overrides)."""
        return {
            "knobs": {k.path: k.default_value for k in self.knobs},
            "prompts": {s.key: s.default for s in self.prompt_slots},
        }

    @property
    def size(self) -> int:
        total = 1
        for knob in self.knobs:
            total *= len(knob.values)
        for slot in self.prompt_slots:
            total *= len(slot.variants)
        return total

    def iter_points(self):
        """Iterate the full cartesian space in declared order (deterministic)."""
        dim_values = [list(k.values) for k in self.knobs] + [
            list(s.variants) for s in self.prompt_slots
        ]
        n_knobs = len(self.knobs)
        for combo in itertools.product(*dim_values):
            yield {
                "knobs": {self.knobs[i].path: combo[i] for i in range(n_knobs)},
                "prompts": {
                    self.prompt_slots[j].key: combo[n_knobs + j]
                    for j in range(len(self.prompt_slots))
                },
            }

    def random_point(self, rng: random.Random) -> dict[str, Any]:
        return {
            "knobs": {k.path: rng.choice(list(k.values)) for k in self.knobs},
            "prompts": {s.key: rng.choice(list(s.variants)) for s in self.prompt_slots},
        }

    def mutate(
        self,
        point: dict[str, Any],
        rng: random.Random,
        n_changes: int = 1,
        local: bool = False,
    ) -> dict[str, Any]:
        """Return a neighboring point with ``n_changes`` dimensions changed.

        ``local=True`` nudges each chosen dimension to an adjacent value
        (declared order, wrapping); otherwise a value is resampled uniformly.
        """
        mutated = {"knobs": dict(point["knobs"]), "prompts": dict(point["prompts"])}
        dims = [("knob", k.path) for k in self.knobs] + [
            ("prompt", s.key) for s in self.prompt_slots
        ]
        for kind, name in rng.sample(dims, k=min(n_changes, len(dims))):
            if kind == "knob":
                dim = self._knob_by_path[name]
                values = list(dim.values)
                current = mutated["knobs"].get(name, dim.default_value)
                idx = values.index(current) if current in values else dim.default_index
                if local:
                    idx = (idx + rng.choice((-1, 1))) % len(values)
                    mutated["knobs"][name] = values[idx]
                else:
                    mutated["knobs"][name] = rng.choice(values)
            else:
                slot = self._slot_by_key[name]
                names = list(slot.variants)
                current = mutated["prompts"].get(name, slot.default)
                idx = names.index(current) if current in names else names.index(slot.default)
                if local:
                    idx = (idx + rng.choice((-1, 1))) % len(names)
                    mutated["prompts"][name] = names[idx]
                else:
                    mutated["prompts"][name] = rng.choice(names)
        return mutated

    # ── encoding ────────────────────────────────────────────────────────

    def encode(self, point: dict[str, Any]) -> str:
        """Canonical JSON encoding (sorted keys — deterministic)."""
        return json.dumps(point, sort_keys=True, separators=(",", ":"))

    def decode(self, content: str) -> dict[str, Any]:
        """Parse a candidate string into a normalized point.

        Missing dimensions fall back to defaults; unknown dimensions are
        dropped (forward compatibility); invalid values for declared
        dimensions raise HarnessSpaceError.
        """
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            raise HarnessSpaceError(f"candidate is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise HarnessSpaceError("candidate must be a JSON object")
        raw_knobs = data.get("knobs", {})
        raw_prompts = data.get("prompts", {})
        if not isinstance(raw_knobs, dict) or not isinstance(raw_prompts, dict):
            raise HarnessSpaceError('"knobs" and "prompts" must be JSON objects')

        point = self.default_point()
        for path, value in raw_knobs.items():
            dim = self._knob_by_path.get(path)
            if dim is None:
                continue
            if value not in dim.values:
                raise HarnessSpaceError(f"invalid value {value!r} for knob {path!r}")
            point["knobs"][path] = value
        for key, variant in raw_prompts.items():
            slot = self._slot_by_key.get(key)
            if slot is None:
                continue
            if variant not in slot.variants:
                raise HarnessSpaceError(f"invalid variant {variant!r} for prompt slot {key!r}")
            point["prompts"][key] = variant
        return point

    # ── config overrides ────────────────────────────────────────────────

    def to_overrides(self, point: dict[str, Any]) -> dict[str, Any]:
        """Flatten a point to dotted-path config overrides.

        Prompt variants mapped to None (the "default" variant) produce no
        override, leaving the built-in template in effect.
        """
        overrides: dict[str, Any] = dict(point["knobs"])
        for slot in self.prompt_slots:
            variant_name = point["prompts"].get(slot.key, slot.default)
            template = slot.variants[variant_name]
            if template is not None:
                overrides[slot.config_path] = template
        return overrides

    # ── (de)serialization for space definitions ─────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "knobs": [
                {"path": k.path, "values": list(k.values), "default_index": k.default_index}
                for k in self.knobs
            ],
            "prompt_slots": [
                {
                    "key": s.key,
                    "config_path": s.config_path,
                    "variants": dict(s.variants),
                    "default": s.default,
                }
                for s in self.prompt_slots
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HarnessSearchSpace":
        knobs = [
            KnobDimension(
                path=k["path"],
                values=tuple(k["values"]),
                default_index=int(k.get("default_index", 0)),
            )
            for k in data.get("knobs", [])
        ]
        slots = [
            PromptSlot(
                key=s["key"],
                config_path=s["config_path"],
                variants=dict(s["variants"]),
                default=s.get("default", "default"),
            )
            for s in data.get("prompt_slots", [])
        ]
        return cls(knobs=knobs, prompt_slots=slots)


def default_harness_space() -> HarnessSearchSpace:
    """The shipped D1 search space: memory/reflection knobs + prompt variants."""
    return HarnessSearchSpace(
        knobs=[
            KnobDimension(
                "memory.pageindex.routing_min_confidence", (0.2, 0.3, 0.4), default_index=1
            ),
            KnobDimension("memory.reflection.max_lessons", (2, 3, 5), default_index=1),
            KnobDimension("memory.reflection.min_generality", (2, 3, 4), default_index=1),
        ],
        prompt_slots=[
            PromptSlot(
                "extraction",
                "memory.wiki.extraction_prompt",
                variants={
                    "default": None,
                    "strict_json": _EXTRACTION_VARIANT_STRICT_JSON,
                    "detailed_citations": _EXTRACTION_VARIANT_DETAILED_CITATIONS,
                },
            ),
            PromptSlot(
                "reflection",
                "memory.reflection.prompt_template",
                variants={
                    "default": None,
                    "failure_first": _REFLECTION_VARIANT_FAILURE_FIRST,
                    "concise": _REFLECTION_VARIANT_CONCISE,
                },
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Eval suite runner (narrow, mockable interface)
# ---------------------------------------------------------------------------


@dataclass
class EvalSuiteResult:
    """Aggregate outcome of one built-in eval suite run."""

    score: float
    total: int
    passed: int
    report_path: str | None = None


class EvalSuiteRunner(Protocol):
    """Narrow interface over the built-in eval suite (fully mockable in tests)."""

    def run_suite(self, overrides: dict[str, Any], limit: int) -> EvalSuiteResult: ...


def apply_overrides(config: Any, overrides: dict[str, Any]) -> None:
    """Apply dotted-path overrides (e.g. ``memory.reflection.max_lessons``).

    Paths come from the declared HarnessSearchSpace, so they are whitelisted
    by construction; unknown paths still raise HarnessSpaceError.
    """
    for path, value in overrides.items():
        parts = path.split(".")
        obj = config
        for part in parts[:-1]:
            obj = getattr(obj, part, None)
            if obj is None:
                raise HarnessSpaceError(f"unknown config path: {path!r}")
        if not hasattr(obj, parts[-1]):
            raise HarnessSpaceError(f"unknown config path: {path!r}")
        setattr(obj, parts[-1], value)


class BuiltinEvalSuiteRunner:
    """Runs the built-in eval suite against a config-overridden QueryLoop.

    The EvoX Evaluator protocol is synchronous, so each suite run bridges into
    a fresh event loop on a dedicated worker thread. Eval results are NOT
    recorded into the shared EvalStore (that would pollute the CI regression
    gate's data); a per-candidate JSON report is written to ``reports_dir``
    instead.
    """

    def __init__(
        self,
        base_config: Any,
        working_dir: str = ".",
        reports_dir: str | Path | None = None,
        probe_timeout: float = 5.0,
    ) -> None:
        self.base_config = base_config
        self.working_dir = working_dir
        self.reports_dir = Path(reports_dir).expanduser() if reports_dir else None
        self.probe_timeout = probe_timeout
        self._report_counter = itertools.count(1)

    async def probe(self) -> None:
        """Raise EvalSuiteError when the model endpoint is not reachable."""
        import httpx

        base_url = getattr(getattr(self.base_config, "llm", None), "base_url", "") or ""
        try:
            async with httpx.AsyncClient(timeout=self.probe_timeout) as client:
                await client.get(base_url)
        except Exception as e:
            raise EvalSuiteError(f"model endpoint {base_url!r} is not reachable: {e}") from e

    def run_suite(self, overrides: dict[str, Any], limit: int) -> EvalSuiteResult:
        config = self.base_config.model_copy(deep=True)
        apply_overrides(config, overrides)
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                cases, results = pool.submit(asyncio.run, self._run_async(config, limit)).result()
            except EvalSuiteError:
                raise
            except Exception as e:
                raise EvalSuiteError(f"eval suite run failed: {e}") from e

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        score = passed / total if total else 0.0
        report_path = self._write_report(overrides, cases, results, score)
        return EvalSuiteResult(score=score, total=total, passed=passed, report_path=report_path)

    async def _run_async(self, config: Any, limit: int) -> tuple[list[Any], list[Any]]:
        from vibe.core.query_loop_factory import QueryLoopFactory
        from vibe.evals.runner import EvalRunner
        from vibe.harness.memory.eval_store import EvalStore

        store = EvalStore()
        cases = store.load_builtin_evals()
        if limit:
            cases = cases[:limit]
        if not cases:
            raise EvalSuiteError("no built-in eval cases found")

        query_loop = QueryLoopFactory(
            base_url=config.llm.base_url,
            model=config.llm.default_model,
            api_key=config.resolve_api_key(),
            working_dir=self.working_dir,
            fallback_chain=config.get_fallback_chain(),
            config=config,
        ).create()
        runner = EvalRunner(query_loop=query_loop, eval_store=None)
        try:
            results = await runner.run_all(cases)
        finally:
            close = getattr(query_loop, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.debug("query loop close failed (non-fatal)", exc_info=True)
        return cases, results

    def _write_report(
        self, overrides: dict[str, Any], cases: list[Any], results: list[Any], score: float
    ) -> str | None:
        if self.reports_dir is None:
            return None
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            path = self.reports_dir / f"candidate_{next(self._report_counter):04d}.json"
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "overrides": overrides,
                "score": score,
                "passed": sum(1 for r in results if r.passed),
                "total": len(results),
                "cases": [
                    {"id": c.id, "passed": r.passed, "diff": r.diff} for c, r in zip(cases, results)
                ],
            }
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            return str(path)
        except Exception as e:
            logger.warning("Failed to write eval report (non-fatal): %s", e)
            return None


# ---------------------------------------------------------------------------
# Harness evaluator (EvoX Evaluator protocol) + provenance log
# ---------------------------------------------------------------------------


class HarnessEvaluator:
    """Map an encoded harness candidate to its built-in eval suite score.

    Matches the synchronous EvoX ``Evaluator`` protocol. All eval/model access
    goes through the injected ``EvalSuiteRunner``, so unit tests need no live
    model endpoint. Every evaluation is appended to the provenance JSONL log
    (overrides + score + acceptance + eval report path) — Meta-Harness's
    "full access to prior candidates' scores/traces" principle.
    """

    def __init__(
        self,
        space: HarnessSearchSpace,
        suite_runner: EvalSuiteRunner,
        baseline_path: str | Path | None = None,
        reference_score: float | None = None,
        provenance_path: str | Path | None = None,
        eval_limit: int = DEFAULT_EVAL_LIMIT,
        max_drop_pct: float = DEFAULT_MAX_DROP_PCT,
    ) -> None:
        self.space = space
        self.suite_runner = suite_runner
        self.baseline_path = baseline_path
        self.reference_score = reference_score
        self.provenance_path = Path(provenance_path).expanduser() if provenance_path else None
        self.eval_limit = eval_limit
        self.max_drop_pct = max_drop_pct
        self._baseline_score: float | None = None

    @property
    def baseline_score(self) -> float:
        if self._baseline_score is None:
            self._baseline_score = load_baseline_score(self.baseline_path)
        return self._baseline_score

    def __call__(self, content: str) -> tuple[float, dict[str, Any]]:
        """EvoX Evaluator entry point. Undecodable candidates score 0.0."""
        try:
            point = self.space.decode(content)
        except HarnessSpaceError as e:
            logger.warning("Skipping undecodable candidate: %s", e)
            return 0.0, {"error": f"invalid candidate encoding: {e}"}
        return self.evaluate(point, content=content)

    def evaluate(
        self, point: dict[str, Any], content: str | None = None
    ) -> tuple[float, dict[str, Any]]:
        """Evaluate a decoded point. Never raises — eval errors score 0.0."""
        overrides = self.space.to_overrides(point)
        if content is None:
            content = self.space.encode(point)
        try:
            result = self.suite_runner.run_suite(overrides, self.eval_limit)
        except Exception as e:
            logger.warning("Eval suite run failed (candidate scored 0.0): %s", e)
            record = self._provenance_record(
                content, point, overrides, score=0.0, decision=None, report_path=None, error=str(e)
            )
            self._append_provenance(record)
            return 0.0, {"overrides": overrides, "accepted": False, "error": str(e)}

        decision = evaluate_acceptance(
            result.score, self.reference_score, self.baseline_score, self.max_drop_pct
        )
        record = self._provenance_record(
            content, point, overrides, result.score, decision, result.report_path, error=None
        )
        self._append_provenance(record)
        logger.info(
            "Harness candidate scored %.4f (accepted=%s: %s)",
            result.score,
            decision.accepted,
            decision.reason,
        )
        return result.score, {
            "overrides": overrides,
            "accepted": decision.accepted,
            "decision_reason": decision.reason,
            "report_path": result.report_path,
            "passed": result.passed,
            "total": result.total,
        }

    def _provenance_record(
        self,
        content: str,
        point: dict[str, Any],
        overrides: dict[str, Any],
        score: float,
        decision: AcceptanceDecision | None,
        report_path: str | None,
        error: str | None,
    ) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "candidate_key": hashlib.sha1(content.encode("utf-8")).hexdigest()[:12],
            "point": point,
            "overrides": overrides,
            "score": score,
            "reference_score": self.reference_score,
            "baseline_score": self.baseline_score,
            "accepted": decision.accepted if decision else False,
            "decision_reason": decision.reason if decision else f"evaluation error: {error}",
            "eval_report_path": report_path,
            "error": error,
        }

    def _append_provenance(self, record: dict[str, Any]) -> None:
        if self.provenance_path is None:
            return
        try:
            self.provenance_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.provenance_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        except Exception as e:
            logger.warning("Failed to append harness provenance log (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Solution generator (EvoX SolutionGenerator protocol)
# ---------------------------------------------------------------------------


class HarnessSolutionGenerator:
    """Generates neighboring points in the declared HarnessSearchSpace.

    Operator semantics: LOCAL_REFINEMENT nudges one dimension to an adjacent
    value, STRUCTURAL_VARIATION resamples half the dimensions, FREE_FORM
    resamples one dimension to any value. Undecodable parents fall back to a
    random valid point, so the loop always evaluates real harness configs.
    Inspiration candidates are unused in this MVP (the space is small enough
    that neighborhood mutation suffices).
    """

    def __init__(self, space: HarnessSearchSpace, seed: int | None = None) -> None:
        self.space = space
        self.rng = random.Random(seed)

    async def generate(
        self,
        parent: str,
        operator: VariationOperator,
        inspiration: list[str],
        problem_description: str,
    ) -> str:
        try:
            point = self.space.decode(parent)
        except HarnessSpaceError:
            point = self.space.random_point(self.rng)

        if operator == VariationOperator.STRUCTURAL_VARIATION:
            n_dims = len(self.space.knobs) + len(self.space.prompt_slots)
            point = self.space.mutate(point, self.rng, n_changes=max(2, n_dims // 2))
        elif operator == VariationOperator.LOCAL_REFINEMENT:
            point = self.space.mutate(point, self.rng, n_changes=1, local=True)
        else:
            point = self.space.mutate(point, self.rng, n_changes=1)
        return self.space.encode(point)
