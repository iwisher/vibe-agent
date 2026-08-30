"""Orchestrator + report tests: full offline Tier-A run over the bundled corpus."""

import json

from vibe.redteam import (
    RedTeamOrchestrator,
    build_report,
    load_corpus,
    render_json,
    render_markdown,
)
from vibe.redteam.corpus import CorpusEntry
from vibe.redteam.oracles import EXECUTORS
from vibe.redteam.orchestrator import TOPIC_REDTEAM
from vibe.swarm.protocol import EventBroker

EXECUTABLE_SURFACES = set(EXECUTORS)

# Pre-confirmed findings documented in the plan (docs/plans/2026-08-29-multi-agent-redteam.md).
# Remediation wave 1 (MCP SSRF gate + SmartApprover untrusted-args fencing) landed;
# the set must stay empty — any entry here is a live, unremediated bypass.
PRE_CONFIRMED_BYPASSES: set[str] = set()


async def test_bundled_corpus_surfaces_all_covered():
    """Every bundled corpus surface must have an executor (memory is Tier-B only)."""
    bundled = {e.surface for e in load_corpus()}
    assert bundled <= EXECUTABLE_SURFACES | {"memory"}


async def test_tier_a_run_defense_holds_on_bundled_corpus():
    broker = EventBroker()
    queue = await broker.subscribe(TOPIC_REDTEAM)
    findings = await RedTeamOrchestrator(load_corpus(), broker=broker).run(
        surfaces=EXECUTABLE_SURFACES
    )
    assert len(findings) > 0
    failures = {f.entry.id for f in findings if not f.passed}
    # Exactly the pre-confirmed bypasses fail — nothing more, nothing less.
    assert failures == PRE_CONFIRMED_BYPASSES
    # One RESULT message per executed entry plus the run-level BROADCAST.
    assert queue.qsize() == len(findings) + 1


async def test_entries_without_executor_reported_as_errors():
    # Every bundled-corpus surface has an executor; a surface without one (memory is
    # reserved for Tier B) must surface as a loud error, never a silent skip.
    corpus = [
        CorpusEntry(
            id="t-noexec",
            surface="memory",
            payload={"note": "tier-b only"},
            expected_outcome="blocked",
            severity="medium",
        )
    ]
    broker = EventBroker()
    queue = await broker.subscribe(TOPIC_REDTEAM)
    findings = await RedTeamOrchestrator(corpus, broker=broker).run()
    assert all(f.observed.outcome == "error" for f in findings)
    assert all(not f.passed for f in findings)
    # No-executor entries still publish their RESULT (reporting symmetry).
    assert queue.qsize() == len(findings) + 1


async def test_report_shape_and_renderers():
    findings = await RedTeamOrchestrator(load_corpus()).run(surfaces=EXECUTABLE_SURFACES)
    report = build_report(findings)
    assert report["total_attacks"] == len(findings)
    assert report["bypasses"] == len(PRE_CONFIRMED_BYPASSES)
    assert set(report["by_surface"]) == EXECUTABLE_SURFACES
    assert "tier_b" not in report

    parsed = json.loads(render_json(findings))
    assert parsed["total_attacks"] == len(findings)

    md = render_markdown(findings)
    assert "# Red-Team Findings Report" in md
    assert "bypasses" in md.lower()


async def test_report_includes_tier_b_section():
    from vibe.redteam.tier_b import TierBResult

    findings = await RedTeamOrchestrator(load_corpus()).run(surfaces=EXECUTABLE_SURFACES)
    tier_b = [
        TierBResult(
            scenario_id="tb-x",
            passed=True,
            layer="pattern_scan",
            side_effect_free=True,
            detail="contained",
        )
    ]
    report = build_report(findings, tier_b)
    assert report["tier_b"]["scenarios"] == 1
    assert report["tier_b"]["contained"] == 1

    md = render_markdown(findings, tier_b)
    assert "## Tier B compromised-model scenarios" in md
    assert "`tb-x`" in md

    parsed = json.loads(render_json(findings, tier_b))
    assert parsed["tier_b"]["results"][0]["id"] == "tb-x"


async def test_markdown_sanitizes_hostile_detail_strings():
    """Attacker-influenced detail text must not inject markdown sections/bullets."""
    from vibe.redteam.tier_b import TierBResult

    findings = await RedTeamOrchestrator(load_corpus()).run(surfaces=EXECUTABLE_SURFACES)
    hostile = TierBResult(
        scenario_id="tb-evil",
        passed=False,
        layer="none\n\n## Fake Section\n- [PASS] `fake`",
        side_effect_free=False,
        detail="line1\n\n## Injected\n- [PASS] `not-real`",
    )
    md = render_markdown(findings, [hostile])
    # Newlines are stripped, so the hostile text survives only as inline prose —
    # it can never form a markdown section or a fake bullet/verdict.
    assert "\n## Fake Section" not in md
    assert "\n## Injected" not in md
    assert md.count("\n## ") == 3  # Bypasses + Tier A + Tier B — nothing injected
    assert "\n- [PASS] `not-real`" not in md
    # Backticks inside hostile fields are neutralized (template backticks stay).
    assert "`not-real`" not in md
    assert "'not-real'" in md
