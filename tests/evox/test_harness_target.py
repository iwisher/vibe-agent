"""Tests for EvoX harness-target evolution (bounded knob/prompt search, D1)."""

from __future__ import annotations

import json
import random

import pytest

from vibe.evox.harness_target import (
    BuiltinEvalSuiteRunner,
    EvalSuiteError,
    EvalSuiteResult,
    HarnessEvaluator,
    HarnessSearchSpace,
    HarnessSolutionGenerator,
    HarnessSpaceError,
    KnobDimension,
    PromptSlot,
    default_harness_space,
    evaluate_acceptance,
    load_baseline_score,
    passes_regression_gate,
)
from vibe.evox.types import VariationOperator


class FakeSuiteRunner:
    """Mock EvalSuiteRunner — no live model needed."""

    def __init__(self, scores=None, error=None):
        self.calls: list[tuple[dict, int]] = []
        self._scores = list(scores) if scores is not None else None
        self._error = error

    def run_suite(self, overrides, limit):
        self.calls.append((overrides, limit))
        if self._error is not None:
            raise self._error
        score = self._scores.pop(0) if self._scores else 0.0
        return EvalSuiteResult(
            score=score, total=20, passed=round(score * 20), report_path="/tmp/report.json"
        )


@pytest.fixture
def space():
    return default_harness_space()


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------


class TestHarnessSearchSpace:
    def test_default_space_matches_spec(self, space):
        knob_paths = [k.path for k in space.knobs]
        assert knob_paths == [
            "memory.pageindex.routing_min_confidence",
            "memory.reflection.max_lessons",
            "memory.reflection.min_generality",
        ]
        values = {k.path: k.values for k in space.knobs}
        assert values["memory.pageindex.routing_min_confidence"] == (0.2, 0.3, 0.4)
        assert values["memory.reflection.max_lessons"] == (2, 3, 5)
        assert values["memory.reflection.min_generality"] == (2, 3, 4)
        slots = {s.key: s for s in space.prompt_slots}
        assert slots["extraction"].config_path == "memory.wiki.extraction_prompt"
        assert slots["reflection"].config_path == "memory.reflection.prompt_template"
        # Two conservative variants each, plus the no-override default
        assert sorted(slots["extraction"].variants) == [
            "default",
            "detailed_citations",
            "strict_json",
        ]
        assert sorted(slots["reflection"].variants) == ["concise", "default", "failure_first"]
        assert slots["extraction"].variants["default"] is None
        assert slots["reflection"].variants["default"] is None
        # Variants must keep the required placeholders
        assert "{transcript}" in slots["extraction"].variants["strict_json"]
        assert "{transcript}" in slots["extraction"].variants["detailed_citations"]
        for name in ("failure_first", "concise"):
            tmpl = slots["reflection"].variants[name]
            for placeholder in ("{max_lessons}", "{outcome}", "{query}", "{transcript}"):
                assert placeholder in tmpl

    def test_default_point_reproduces_shipped_config(self, space):
        point = space.default_point()
        assert point["knobs"] == {
            "memory.pageindex.routing_min_confidence": 0.3,
            "memory.reflection.max_lessons": 3,
            "memory.reflection.min_generality": 3,
        }
        assert point["prompts"] == {"extraction": "default", "reflection": "default"}
        # Default point produces no prompt overrides at all
        overrides = space.to_overrides(point)
        assert "memory.wiki.extraction_prompt" not in overrides
        assert "memory.reflection.prompt_template" not in overrides

    def test_iter_points_deterministic_and_complete(self, space):
        first = list(space.iter_points())
        second = list(space.iter_points())
        assert first == second
        assert len(first) == space.size == 3 * 3 * 3 * 3 * 3
        assert space.default_point() in first
        # Iteration follows declared dimension/value order (index 0 first)
        assert first[0]["knobs"] == {
            "memory.pageindex.routing_min_confidence": 0.2,
            "memory.reflection.max_lessons": 2,
            "memory.reflection.min_generality": 2,
        }
        assert len({space.encode(p) for p in first}) == space.size  # all unique

    def test_encode_decode_roundtrip(self, space):
        point = {
            "knobs": {
                "memory.pageindex.routing_min_confidence": 0.4,
                "memory.reflection.max_lessons": 5,
                "memory.reflection.min_generality": 2,
            },
            "prompts": {"extraction": "strict_json", "reflection": "concise"},
        }
        encoded = space.encode(point)
        assert encoded == space.encode(point)  # deterministic
        assert space.decode(encoded) == point

    def test_decode_fills_defaults_and_drops_unknown(self, space):
        point = space.decode('{"knobs": {"memory.reflection.max_lessons": 5}, "unknown": 1}')
        assert point["knobs"]["memory.reflection.max_lessons"] == 5
        assert point["knobs"]["memory.reflection.min_generality"] == 3  # default filled
        assert "unknown" not in point

    def test_decode_rejects_invalid_values(self, space):
        with pytest.raises(HarnessSpaceError):
            space.decode('{"knobs": {"memory.reflection.max_lessons": 4}}')
        with pytest.raises(HarnessSpaceError):
            space.decode("not json at all")
        with pytest.raises(HarnessSpaceError):
            space.decode('{"prompts": {"extraction": "nonexistent_variant"}}')

    def test_to_overrides_maps_prompt_variants(self, space):
        point = space.default_point()
        point["prompts"]["extraction"] = "strict_json"
        overrides = space.to_overrides(point)
        assert (
            overrides["memory.wiki.extraction_prompt"]
            == space.prompt_slots[0].variants["strict_json"]
        )
        assert "memory.reflection.prompt_template" not in overrides

    def test_from_dict_roundtrip(self, space):
        clone = HarnessSearchSpace.from_dict(space.to_dict())
        assert clone.size == space.size
        assert clone.default_point() == space.default_point()
        assert clone.to_dict() == space.to_dict()

    def test_space_validation(self):
        with pytest.raises(HarnessSpaceError):
            HarnessSearchSpace(knobs=[], prompt_slots=[])
        with pytest.raises(HarnessSpaceError):
            HarnessSearchSpace(knobs=[KnobDimension("a.b", ())], prompt_slots=[])
        with pytest.raises(HarnessSpaceError):
            HarnessSearchSpace(
                knobs=[KnobDimension("a.b", (1, 2), default_index=5)], prompt_slots=[]
            )
        with pytest.raises(HarnessSpaceError):
            HarnessSearchSpace(
                knobs=[], prompt_slots=[PromptSlot("x", "a.b", variants={"v": "tmpl"})]
            )

    def test_mutate_deterministic_with_seed(self, space):
        point = space.default_point()
        m1 = space.mutate(point, random.Random(7), n_changes=1)
        m2 = space.mutate(point, random.Random(7), n_changes=1)
        assert m1 == m2

    def test_mutate_local_changes_exactly_one_dimension_to_neighbor(self, space):
        point = space.default_point()
        mutated = space.mutate(point, random.Random(3), n_changes=1, local=True)
        diffs = [
            name
            for name in set(mutated["knobs"]) | set(point["knobs"])
            if mutated["knobs"][name] != point["knobs"][name]
        ] + [
            key
            for key in set(mutated["prompts"]) | set(point["prompts"])
            if mutated["prompts"][key] != point["prompts"][key]
        ]
        assert len(diffs) == 1
        name = diffs[0]
        if name in mutated["knobs"]:
            values = next(k.values for k in space.knobs if k.path == name)
            old_idx = list(values).index(point["knobs"][name])
            new_idx = list(values).index(mutated["knobs"][name])
            assert abs(new_idx - old_idx) == 1 or {old_idx, new_idx} == {0, len(values) - 1}


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------


class TestAcceptanceGate:
    def test_regression_gate_matches_ci_convention(self):
        # scripts/ci_eval_report.py: current >= baseline * 0.95
        assert passes_regression_gate(0.95, 1.0) is True
        assert passes_regression_gate(0.949, 1.0) is False
        assert passes_regression_gate(0.5, 0.5, max_drop_pct=0.05) is True

    def test_accepts_improvement_within_bound(self):
        d = evaluate_acceptance(0.6, reference_score=0.5, baseline_score=0.5)
        assert d.accepted and d.improved and d.within_regression_bound

    def test_rejects_non_improvement(self):
        d = evaluate_acceptance(0.5, reference_score=0.5, baseline_score=0.5)
        assert not d.accepted and d.improved is False
        assert "no improvement" in d.reason

    def test_rejects_regression_beyond_5pct_even_when_improved(self):
        d = evaluate_acceptance(0.8, reference_score=0.5, baseline_score=1.0)
        assert d.improved is True and d.within_regression_bound is False
        assert not d.accepted

    def test_no_reference_never_accepts(self):
        d = evaluate_acceptance(1.0, reference_score=None, baseline_score=1.0)
        assert not d.accepted and d.improved is None

    def test_load_baseline_score(self, tmp_path):
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"overall_score": 0.82}))
        assert load_baseline_score(path) == 0.82
        assert load_baseline_score(tmp_path / "missing.json") == 1.0
        assert load_baseline_score(None) == 1.0


# ---------------------------------------------------------------------------
# Evaluator (fully mocked — no live model)
# ---------------------------------------------------------------------------


class TestHarnessEvaluator:
    def _evaluator(self, runner, tmp_path, reference=0.5, baseline=0.5):
        baseline_path = tmp_path / "baseline_scorecard.json"
        baseline_path.write_text(json.dumps({"overall_score": baseline}))
        return HarnessEvaluator(
            default_harness_space(),
            runner,
            baseline_path=baseline_path,
            reference_score=reference,
            provenance_path=tmp_path / "prov.jsonl",
            eval_limit=20,
        )

    def test_maps_candidate_to_overrides(self, tmp_path):
        runner = FakeSuiteRunner(scores=[0.6])
        ev = self._evaluator(runner, tmp_path)
        space = ev.space
        point = space.default_point()
        point["knobs"]["memory.reflection.max_lessons"] = 5
        point["prompts"]["reflection"] = "failure_first"
        score, artifacts = ev(space.encode(point))

        assert score == 0.6
        overrides, limit = runner.calls[0]
        assert limit == 20
        assert overrides["memory.reflection.max_lessons"] == 5
        assert overrides["memory.pageindex.routing_min_confidence"] == 0.3
        assert (
            overrides["memory.reflection.prompt_template"]
            == space.prompt_slots[1].variants["failure_first"]
        )
        assert "memory.wiki.extraction_prompt" not in overrides
        assert artifacts["overrides"] == overrides

    def test_accepts_improvement(self, tmp_path):
        runner = FakeSuiteRunner(scores=[0.6])
        ev = self._evaluator(runner, tmp_path, reference=0.5, baseline=0.5)
        _, artifacts = ev.evaluate(ev.space.default_point())
        assert artifacts["accepted"] is True

    def test_rejects_non_improvement(self, tmp_path):
        runner = FakeSuiteRunner(scores=[0.5])
        ev = self._evaluator(runner, tmp_path, reference=0.5, baseline=0.5)
        _, artifacts = ev.evaluate(ev.space.default_point())
        assert artifacts["accepted"] is False
        assert "no improvement" in artifacts["decision_reason"]

    def test_rejects_regression_over_5pct(self, tmp_path):
        runner = FakeSuiteRunner(scores=[0.8])
        ev = self._evaluator(runner, tmp_path, reference=0.5, baseline=1.0)
        _, artifacts = ev.evaluate(ev.space.default_point())
        assert artifacts["accepted"] is False
        assert "regression" in artifacts["decision_reason"]

    def test_suite_error_never_raises(self, tmp_path):
        runner = FakeSuiteRunner(error=EvalSuiteError("endpoint down"))
        ev = self._evaluator(runner, tmp_path)
        score, artifacts = ev.evaluate(ev.space.default_point())
        assert score == 0.0
        assert artifacts["accepted"] is False
        assert "endpoint down" in artifacts["error"]

    def test_invalid_candidate_encoding(self, tmp_path):
        runner = FakeSuiteRunner(scores=[0.9])
        ev = self._evaluator(runner, tmp_path)
        score, artifacts = ev("definitely not json")
        assert score == 0.0
        assert "invalid candidate encoding" in artifacts["error"]
        assert runner.calls == []  # never reached the suite
        assert not (tmp_path / "prov.jsonl").exists()  # nothing evaluated → nothing logged

    def test_provenance_jsonl_written(self, tmp_path):
        runner = FakeSuiteRunner(scores=[0.5, 0.6])
        ev = self._evaluator(runner, tmp_path, reference=0.5, baseline=0.5)
        space = ev.space
        ev.evaluate(space.default_point())
        point = space.mutate(space.default_point(), random.Random(1))
        ev.evaluate(point)

        lines = (tmp_path / "prov.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        first, second = (json.loads(line) for line in lines)
        assert first["score"] == 0.5
        assert first["reference_score"] == 0.5
        assert first["baseline_score"] == 0.5
        assert first["accepted"] is False
        assert first["eval_report_path"] == "/tmp/report.json"
        assert first["overrides"]["memory.reflection.max_lessons"] == 3
        assert first["error"] is None
        assert second["accepted"] is True
        assert (
            second["candidate_key"] != first["candidate_key"] or second["point"] != first["point"]
        )


# ---------------------------------------------------------------------------
# Solution generator
# ---------------------------------------------------------------------------


class TestHarnessSolutionGenerator:
    async def test_deterministic_with_seed(self, space):
        parent = space.encode(space.default_point())
        g1 = HarnessSolutionGenerator(space, seed=42)
        g2 = HarnessSolutionGenerator(space, seed=42)
        c1 = await g1.generate(parent, VariationOperator.FREE_FORM, [], "p")
        c2 = await g2.generate(parent, VariationOperator.FREE_FORM, [], "p")
        assert c1 == c2
        space.decode(c1)  # valid encoding

    async def test_undecodable_parent_falls_back_to_valid_point(self, space):
        gen = HarnessSolutionGenerator(space, seed=1)
        child = await gen.generate("garbage{{{", VariationOperator.FREE_FORM, [], "p")
        space.decode(child)  # must be a valid point

    async def test_local_refinement_stays_in_neighborhood(self, space):
        parent_point = space.default_point()
        parent = space.encode(parent_point)
        gen = HarnessSolutionGenerator(space, seed=9)
        child = space.decode(
            await gen.generate(parent, VariationOperator.LOCAL_REFINEMENT, [], "p")
        )
        changed = [k for k in child["knobs"] if child["knobs"][k] != parent_point["knobs"][k]] + [
            k for k in child["prompts"] if child["prompts"][k] != parent_point["prompts"][k]
        ]
        assert len(changed) == 1


# ---------------------------------------------------------------------------
# Production runner (construction/override plumbing only — no live evals)
# ---------------------------------------------------------------------------


class TestBuiltinEvalSuiteRunner:
    def test_apply_overrides_validates_paths(self):
        from vibe.core.config import VibeConfig
        from vibe.evox.harness_target import apply_overrides

        config = VibeConfig()
        apply_overrides(
            config,
            {
                "memory.reflection.max_lessons": 5,
                "memory.pageindex.routing_min_confidence": 0.4,
                "memory.reflection.prompt_template": "custom {transcript}",
            },
        )
        assert config.memory.reflection.max_lessons == 5
        assert config.memory.pageindex.routing_min_confidence == 0.4
        assert config.memory.reflection.prompt_template == "custom {transcript}"

        with pytest.raises(HarnessSpaceError):
            apply_overrides(VibeConfig(), {"memory.nonexistent.field": 1})
        with pytest.raises(HarnessSpaceError):
            apply_overrides(VibeConfig(), {"memory.reflection.nope": 1})

    async def test_probe_raises_eval_suite_error_when_unreachable(self):
        from vibe.core.config import VibeConfig

        config = VibeConfig()
        config.llm.base_url = "http://127.0.0.1:9/unreachable"  # port 9: discard
        runner = BuiltinEvalSuiteRunner(base_config=config, probe_timeout=1.0)
        with pytest.raises(EvalSuiteError, match="not reachable"):
            await runner.probe()
