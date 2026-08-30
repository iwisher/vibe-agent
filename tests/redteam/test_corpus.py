"""Corpus schema validation tests: fail fast on malformed entries."""

import pytest

from vibe.redteam.corpus import (
    CorpusValidationError,
    load_corpus,
    load_corpus_file,
)


def test_bundled_corpus_loads_cleanly():
    entries = load_corpus()
    assert len(entries) >= 10
    assert all(e.id and e.surface and e.expected_outcome for e in entries)


def test_missing_required_key_fails_fast(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("entries:\n  - id: x\n    surface: ssrf\n    payload: u\n    severity: low\n")
    with pytest.raises(CorpusValidationError, match="missing required keys"):
        load_corpus_file(f)


def test_unknown_surface_rejected(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text(
        "entries:\n  - id: x\n    surface: nope\n    payload: u\n"
        "    expected_outcome: blocked\n    severity: low\n"
    )
    with pytest.raises(CorpusValidationError, match="unknown surface"):
        load_corpus_file(f)


def test_unknown_outcome_and_severity_rejected(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text(
        "entries:\n  - id: x\n    surface: ssrf\n    payload: u\n"
        "    expected_outcome: vaporized\n    severity: catastrophic\n"
    )
    with pytest.raises(CorpusValidationError, match="unknown expected_outcome"):
        load_corpus_file(f)


def test_duplicate_ids_rejected(tmp_path):
    f = tmp_path / "dup.yaml"
    f.write_text(
        "entries:\n"
        "  - {id: x, surface: ssrf, payload: u, expected_outcome: blocked, severity: low}\n"
        "  - {id: x, surface: ssrf, payload: v, expected_outcome: blocked, severity: low}\n"
    )
    with pytest.raises(CorpusValidationError, match="duplicate id"):
        load_corpus_file(f)


def test_top_level_shape_enforced(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("- just\n- a\n- list\n")
    with pytest.raises(CorpusValidationError, match="entries"):
        load_corpus_file(f)


def test_unknown_keys_rejected(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text(
        "entries:\n  - id: x\n    surface: ssrf\n    payload: u\n"
        "    expected_outcome: blocked\n    severity: low\n    note: typo-of-notes\n"
    )
    with pytest.raises(CorpusValidationError, match="unknown keys"):
        load_corpus_file(f)


def test_empty_corpus_dir_rejected(tmp_path):
    with pytest.raises(CorpusValidationError, match="no corpus files"):
        load_corpus(tmp_path)
