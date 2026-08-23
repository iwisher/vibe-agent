"""Tests for the `vibe evox run` CLI wiring (harness target + untouched defaults)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from vibe.core.config import VibeConfig
from vibe.evox.cli import evox_app
from vibe.evox.harness_target import EvalSuiteError, EvalSuiteResult

runner = CliRunner()


class FakeSuiteRunner:
    """Stands in for BuiltinEvalSuiteRunner — no live model endpoint needed.

    The unmodified harness (all-default overrides) scores ``reference``; any
    mutated candidate scores ``mutated``.
    """

    reference = 0.5
    mutated = 0.7
    probe_error: Exception | None = None
    instances: list["FakeSuiteRunner"] = []

    def __init__(self, base_config=None, working_dir=".", reports_dir=None, **kwargs):
        self.base_config = base_config
        self.reports_dir = reports_dir
        self.calls: list[dict] = []
        FakeSuiteRunner.instances.append(self)

    async def probe(self):
        if FakeSuiteRunner.probe_error is not None:
            raise FakeSuiteRunner.probe_error

    def run_suite(self, overrides, limit):
        self.calls.append(overrides)
        is_default = (
            overrides.get("memory.reflection.max_lessons") == 3
            and overrides.get("memory.pageindex.routing_min_confidence") == 0.3
            and overrides.get("memory.reflection.min_generality") == 3
            and "memory.wiki.extraction_prompt" not in overrides
            and "memory.reflection.prompt_template" not in overrides
        )
        score = FakeSuiteRunner.reference if is_default else FakeSuiteRunner.mutated
        return EvalSuiteResult(score=score, total=20, passed=round(score * 20))


@pytest.fixture(autouse=True)
def _patch_harness(monkeypatch, tmp_path):
    """Stub the live-model seams of the harness CLI branch."""
    FakeSuiteRunner.instances = []
    FakeSuiteRunner.reference = 0.5
    FakeSuiteRunner.mutated = 0.7
    FakeSuiteRunner.probe_error = None
    monkeypatch.setattr("vibe.evox.cli.BuiltinEvalSuiteRunner", FakeSuiteRunner)
    monkeypatch.setattr("vibe.evox.cli._load_harness_config", lambda: VibeConfig())
    monkeypatch.setattr("vibe.evox.cli._find_baseline_path", lambda: None)
    yield


def _invoke(args):
    # evox_app has a single command, so standalone it runs in single-command
    # mode (no "run" argv token); under the root CLI it is `vibe evox run`.
    return runner.invoke(evox_app, args)


class TestHarnessTarget:
    def test_wiring_selects_harness_space_and_evaluator(self, tmp_path):
        out = tmp_path / "evox"
        result = _invoke(
            ["--target", "harness", "--iterations", "4", "--limit", "5", "--output-dir", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert "Reference score (unmodified harness): 50.00%" in result.output
        assert "Best score:" in result.output

        # The harness evaluator wrote the provenance JSONL under the output dir
        provenance = out / "harness_candidates.jsonl"
        assert provenance.exists()
        records = [json.loads(line) for line in provenance.read_text().strip().split("\n")]
        # 1 reference + 4 iterations (seeded population avoids random-seed evals)
        assert len(records) == 5
        for record in records:
            assert "overrides" in record and "score" in record and "accepted" in record
        # Reference record first, then mutated candidates
        assert records[0]["overrides"]["memory.reflection.max_lessons"] == 3

        # The runner stub was constructed from the loaded config
        assert FakeSuiteRunner.instances, "BuiltinEvalSuiteRunner was not used"

    def test_accepted_candidate_writes_overrides(self, tmp_path, monkeypatch):
        baseline = tmp_path / "baseline_scorecard.json"
        baseline.write_text(json.dumps({"overall_score": 0.5}))
        monkeypatch.setattr("vibe.evox.cli._find_baseline_path", lambda: baseline)
        out = tmp_path / "evox"
        result = _invoke(["--target", "harness", "--iterations", "6", "--output-dir", str(out)])
        assert result.exit_code == 0, result.output
        # Mutated candidates (0.7) improve over the reference (0.5) and stay
        # within 5% of the 0.5 baseline → accepted.
        assert "ACCEPTED" in result.output
        accepted = json.loads((out / "accepted_overrides.json").read_text())
        assert isinstance(accepted, dict) and accepted

    def test_unreachable_endpoint_clean_failure(self, tmp_path):
        FakeSuiteRunner.probe_error = EvalSuiteError(
            "model endpoint 'http://x' is not reachable: Connection refused"
        )
        result = _invoke(["--target", "harness", "--output-dir", str(tmp_path / "evox")])
        assert result.exit_code == 1
        output = result.output + getattr(result, "stderr", "")
        assert "not reachable" in output
        assert "Traceback" not in output

    def test_eval_infrastructure_error_clean_failure(self, tmp_path, monkeypatch):
        class BrokenRunner(FakeSuiteRunner):
            def run_suite(self, overrides, limit):
                raise EvalSuiteError("eval suite run failed: boom")

        monkeypatch.setattr("vibe.evox.cli.BuiltinEvalSuiteRunner", BrokenRunner)
        result = _invoke(["--target", "harness", "--output-dir", str(tmp_path / "evox")])
        assert result.exit_code == 1
        output = result.output + getattr(result, "stderr", "")
        assert "boom" in output
        assert "Traceback" not in output


class TestDefaultTargetsUnchanged:
    def test_toy_string_target_still_works(self):
        result = _invoke(["--target", "hello", "--iterations", "3"])
        assert result.exit_code == 0, result.output
        assert "Best score:" in result.output
        assert "Strategy switches:" in result.output

    def test_unknown_evaluator_rejected(self):
        result = _invoke(["--evaluator", "nope", "--target", "hello", "--iterations", "1"])
        assert result.exit_code != 0
