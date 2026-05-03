"""Tests for EvalStore and builtin eval cases."""

import json
import sqlite3
from pathlib import Path

import pytest

from vibe.harness.memory.eval_store import EvalCase, EvalResult, EvalStore


@pytest.fixture
def temp_eval_store(tmp_path):
    db_path = tmp_path / "evals.db"
    evals_dir = Path(__file__).parent.parent / "vibe" / "evals" / "builtin"
    return EvalStore(db_path=str(db_path), evals_dir=str(evals_dir))


def test_load_builtin_evals_count(temp_eval_store):
    cases = temp_eval_store.load_builtin_evals()
    assert len(cases) >= 10


def test_load_builtin_evals_ids(temp_eval_store):
    cases = temp_eval_store.load_builtin_evals()
    ids = {c.id for c in cases}
    expected_ids = {
        "file-read-001",
        "bash-math-001",
        "multi-step-001",
        "file-edit-001",
        "bash-stats-001",
        "tool-selection-001",
        "instruction-following-001",
        "multi-step-002",
        "security-hook-001",
        "error-recovery-001",
    }
    assert expected_ids.issubset(ids), f"Missing IDs: {expected_ids - ids}"


def test_load_builtin_evals_structure(temp_eval_store):
    cases = temp_eval_store.load_builtin_evals()
    for case in cases:
        assert isinstance(case, EvalCase)
        assert case.id
        assert isinstance(case.tags, list)
        assert "input" in case.input or "prompt" in case.input
        assert case.expected
        assert isinstance(case.optimization_set, bool)
        assert isinstance(case.holdout_set, bool)


def test_save_and_load_eval(temp_eval_store):
    case = EvalCase(
        id="test-case-001",
        tags=["test"],
        input={"prompt": "hello"},
        expected={"response_contains": "hello"},
        optimization_set=True,
        holdout_set=False,
    )
    temp_eval_store.save_eval(case)
    cases = temp_eval_store.load_builtin_evals()
    assert any(c.id == "test-case-001" for c in cases) is False
    with sqlite3.connect(temp_eval_store.db_path) as conn:
        row = conn.execute("SELECT * FROM evals WHERE id = ?", (case.id,)).fetchone()
        assert row is not None
        assert json.loads(row[1]) == ["test"]


def test_record_result_and_summary(temp_eval_store):
    result = EvalResult(eval_id="bash-math-001", passed=True, diff={})
    temp_eval_store.record_result(result)
    results = temp_eval_store.get_results(eval_id="bash-math-001")
    assert len(results) == 1
    assert results[0]["passed"] == 1
    summary = temp_eval_store.summary()
    assert summary["total_runs"] == 1
    assert summary["passed"] == 1
    assert summary["score"] == 1.0


def test_get_results_all(temp_eval_store):
    temp_eval_store.record_result(EvalResult(eval_id="a", passed=True, diff={}))
    temp_eval_store.record_result(EvalResult(eval_id="b", passed=False, diff={"reason": "fail"}))
    all_results = temp_eval_store.get_results()
    assert len(all_results) == 2
