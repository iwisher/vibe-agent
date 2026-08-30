"""Tier-A executor + oracle tests against the real defense components."""

import pytest

from vibe.redteam.corpus import CorpusEntry
from vibe.redteam.oracles import (
    Observation,
    check_oracle,
    execute_bash_patterns,
    execute_file_safety,
    execute_ssrf,
)


def _entry(surface: str, payload, expected: str) -> CorpusEntry:
    return CorpusEntry(
        id="t", surface=surface, payload=payload, expected_outcome=expected, severity="high"
    )


# ── Oracles ──────────────────────────────────────────────────────────────────


def test_oracle_pass_and_fail():
    e = _entry("ssrf", "u", "blocked")
    assert check_oracle(e, Observation(outcome="blocked")).passed is True
    fail = check_oracle(e, Observation(outcome="allowed"))
    assert fail.passed is False and fail.severity == "high"


def test_oracle_error_is_never_a_pass():
    e = _entry("ssrf", "u", "allowed")
    assert check_oracle(e, Observation(outcome="error")).passed is False


# ── S1 PatternEngine ─────────────────────────────────────────────────────────


def test_s1_dangerous_flagged():
    obs = execute_bash_patterns(_entry("bash_patterns", "rm -rf /", "flagged"))
    assert obs.outcome == "flagged"


def test_s1_benign_allowed():
    obs = execute_bash_patterns(_entry("bash_patterns", "ls -la src/", "allowed"))
    assert obs.outcome == "allowed"


# ── S2 FileSafetyGuard ───────────────────────────────────────────────────────


def test_s2_traversal_blocked():
    obs = execute_file_safety(_entry("file_safety", {"path": "../../../../etc/pwn"}, "blocked"))
    assert obs.outcome == "blocked"


def test_s2_symlink_escape_blocked():
    obs = execute_file_safety(
        _entry("file_safety", {"path": "link-out", "resolve_to": "/etc"}, "blocked")
    )
    assert obs.outcome == "blocked"


def test_s2_benign_inside_jail_allowed():
    obs = execute_file_safety(_entry("file_safety", {"path": "notes.txt"}, "allowed"))
    assert obs.outcome == "allowed"


# ── S3 SSRF guard ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/admin",
        "http://2130706433/",
        "http://example.com@127.0.0.1/",
        "http://[::ffff:7f00:1]/",
    ],
)
def test_s3_hostile_urls_blocked(url):
    obs = execute_ssrf(_entry("ssrf", url, "blocked"))
    assert obs.outcome == "blocked", f"guard failed to block {url}: {obs.detail}"


def test_s3_benign_allowed():
    obs = execute_ssrf(_entry("ssrf", "https://example.com/docs", "allowed"))
    assert obs.outcome == "allowed"


# ── Malformed payloads surface as errors, never vacuous verdicts ─────────────


def test_missing_command_key_is_error_not_vacuous_pass():
    obs = execute_bash_patterns(_entry("bash_patterns", {"cmd": "rm -rf /"}, "flagged"))
    assert obs.outcome == "error" and "malformed" in obs.detail


def test_missing_url_key_is_error():
    obs = execute_ssrf(_entry("ssrf", {"uri": "http://x"}, "blocked"))
    assert obs.outcome == "error" and "malformed" in obs.detail


def test_missing_path_key_is_error():
    obs = execute_file_safety(_entry("file_safety", {"p": "x"}, "blocked"))
    assert obs.outcome == "error" and "malformed" in obs.detail


def test_traversing_probe_setup_rejected():
    """The executor's own symlink setup must never mkdir outside the jail."""
    obs = execute_file_safety(
        _entry("file_safety", {"path": "../../x", "resolve_to": "/etc"}, "blocked")
    )
    assert obs.outcome == "error" and "malformed" in obs.detail
