"""Gate calibration corpus: schema, composition, and Layer-1 tagging invariants.

The corpus lives outside vibe/redteam/corpus/ because the bundled oracle
semantics cannot express "dangerous but not pattern-matched" offline (see the
corpus file header). These tests keep its ground-truth labels honest.
"""

from pathlib import Path

import pytest

from vibe.redteam.corpus import load_corpus_file
from vibe.tools.security.patterns import PatternEngine, PatternSeverity

GATE_CORPUS = (
    Path(__file__).resolve().parent.parent.parent
    / "vibe"
    / "redteam"
    / "corpus_gate"
    / "gate_calibration.yaml"
)

HOSTILE_OUTCOMES = {"blocked", "flagged"}


@pytest.fixture(scope="module")
def entries():
    return load_corpus_file(GATE_CORPUS)


def test_gate_corpus_loads_cleanly(entries):
    assert len(entries) == 40
    ids = [e.id for e in entries]
    assert len(set(ids)) == len(ids), "duplicate entry ids"


def test_gate_corpus_all_entries_gate_compatible(entries):
    """eval_fast_gate skips entries without payload.tool + payload.args."""
    for e in entries:
        assert isinstance(e.payload, dict), e.id
        assert isinstance(e.payload.get("tool"), str), e.id
        assert isinstance(e.payload.get("args"), dict), e.id


def test_gate_corpus_composition(entries):
    hostile = [e for e in entries if e.expected_outcome in HOSTILE_OUTCOMES]
    benign = [e for e in entries if e.expected_outcome == "allowed"]
    assert len(hostile) == 23
    assert len(benign) == 17
    # Enough benign samples for an FPR reading with some resolution.
    assert len(benign) >= 15
    # The gate's value zone: hostile entries that evade Layer 1 must be the
    # largest hostile class.
    l1_clean_hostile = [e for e in hostile if "l1-clean" in e.notes or "l1-na" in e.notes]
    assert len(l1_clean_hostile) >= 12
    # Severity hygiene: hostile entries are never "low", benign never "critical".
    assert all(e.severity in {"high", "critical"} for e in hostile)
    assert all(e.severity in {"low", "medium"} for e in benign)


def test_gate_corpus_l1_tags_match_pattern_engine(entries):
    """notes tags 'l1-covered'/'l1-clean' must match PatternEngine behavior.

    If patterns.py evolves (new pattern catches an 'l1-clean' entry, or a fix
    stops flagging an 'l1-covered' one), this fails loudly so the corpus notes
    — and the threshold-sweep interpretation — get updated with it.
    """
    engine = PatternEngine()
    tagged = 0
    for e in entries:
        command = e.payload["args"].get("command") if isinstance(e.payload, dict) else None
        if not isinstance(command, str):
            assert "l1-covered" not in e.notes and "l1-clean" not in e.notes, e.id
            continue
        hits = [
            m
            for m in engine.scan(command)
            if m.severity in (PatternSeverity.CRITICAL, PatternSeverity.WARNING)
        ]
        if "l1-covered" in e.notes:
            tagged += 1
            assert hits, f"{e.id}: tagged l1-covered but PatternEngine is clean"
        elif "l1-clean" in e.notes:
            tagged += 1
            assert not hits, (
                f"{e.id}: tagged l1-clean but PatternEngine flags {[m.pattern_id for m in hits]}"
            )
    assert tagged >= 30, "most bash entries should carry an explicit L1 tag"
