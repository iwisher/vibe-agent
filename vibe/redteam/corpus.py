"""Corpus loading and schema validation for red-team attack payloads.

Every corpus YAML entry must carry the five required keys (``id``, ``surface``,
``payload``, ``expected_outcome``, ``severity``). Loading fails fast on the first
malformed entry so a broken corpus can never silently skip attacks.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

CORPUS_DIR = Path(__file__).parent / "corpus"

#: Attack surfaces recognized by the red-team harness (see plan doc §1).
VALID_SURFACES = {
    "bash_patterns",  # S1 — PatternEngine / BashSandbox
    "file_safety",  # S2 — FileSafetyGuard jail
    "ssrf",  # S3 — SSRFGuard / is_safe_url
    "approval",  # S4 — HumanApprover / SmartApprover
    "skill_supply",  # S5 — SkillValidator / SkillInstaller
    "memory",  # S6 — wiki/lesson ingestion poisoning
    "mcp",  # S7 — MCPBridge transport
}

#: What the defense is expected to do with the payload.
VALID_EXPECTED_OUTCOMES = {
    "blocked",  # hard deny (exception / guard rejection)
    "flagged",  # detected and reported, blocks the action
    "warned",  # detected as a warning only — does NOT block (distinct from flagged)
    "allowed",  # benign control case — defense must NOT fire
}

VALID_SEVERITIES = {"low", "medium", "high", "critical"}

REQUIRED_KEYS = {"id", "surface", "payload", "expected_outcome", "severity"}


class CorpusEntry(BaseModel):
    """One attack payload with its expected defensive outcome."""

    id: str
    surface: str
    payload: str | dict[str, Any]
    expected_outcome: str
    severity: str
    notes: str = ""

    @field_validator("surface")
    @classmethod
    def _check_surface(cls, v: str) -> str:
        if v not in VALID_SURFACES:
            raise ValueError(f"unknown surface {v!r}, expected one of {sorted(VALID_SURFACES)}")
        return v

    @field_validator("expected_outcome")
    @classmethod
    def _check_outcome(cls, v: str) -> str:
        if v not in VALID_EXPECTED_OUTCOMES:
            raise ValueError(
                f"unknown expected_outcome {v!r}, expected one of {sorted(VALID_EXPECTED_OUTCOMES)}"
            )
        return v

    @field_validator("severity")
    @classmethod
    def _check_severity(cls, v: str) -> str:
        if v not in VALID_SEVERITIES:
            raise ValueError(f"unknown severity {v!r}, expected one of {sorted(VALID_SEVERITIES)}")
        return v


class CorpusValidationError(ValueError):
    """Raised when a corpus file or entry fails schema validation."""


def load_corpus_file(path: Path) -> list[CorpusEntry]:
    """Load one corpus YAML file, failing fast on the first malformed entry."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CorpusValidationError(f"{path.name}: invalid YAML: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise CorpusValidationError(
            f"{path.name}: top level must be a mapping with an 'entries' list"
        )

    entries: list[CorpusEntry] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(data["entries"]):
        where = f"{path.name} entry #{i}"
        if not isinstance(raw, dict):
            raise CorpusValidationError(
                f"{where}: entry must be a mapping, got {type(raw).__name__}"
            )
        missing = REQUIRED_KEYS - raw.keys()
        if missing:
            raise CorpusValidationError(f"{where}: missing required keys {sorted(missing)}")
        unknown = set(raw) - REQUIRED_KEYS - {"notes"}
        if unknown:
            raise CorpusValidationError(f"{where}: unknown keys {sorted(unknown)}")
        try:
            entry = CorpusEntry(**{k: raw[k] for k in REQUIRED_KEYS}, notes=raw.get("notes", ""))
        except ValueError as e:
            raise CorpusValidationError(f"{where}: {e}") from e
        if entry.id in seen_ids:
            raise CorpusValidationError(f"{where}: duplicate id {entry.id!r}")
        seen_ids.add(entry.id)
        entries.append(entry)
    return entries


def load_corpus(corpus_dir: Path | None = None) -> list[CorpusEntry]:
    """Load every corpus file under ``corpus_dir`` (defaults to the bundled corpus)."""
    root = corpus_dir or CORPUS_DIR
    if not root.is_dir():
        raise CorpusValidationError(f"corpus directory not found: {root}")
    files = sorted(root.glob("*.yaml"))
    if not files:
        raise CorpusValidationError(f"no corpus files found under {root}")
    entries: list[CorpusEntry] = []
    ids: set[str] = set()
    for f in files:
        for entry in load_corpus_file(f):
            if entry.id in ids:
                raise CorpusValidationError(
                    f"duplicate corpus id {entry.id!r} across files ({f.name})"
                )
            ids.add(entry.id)
            entries.append(entry)
    return entries
