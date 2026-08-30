"""Multi-agent adversarial red-team harness for vibe-agent.

See docs/plans/2026-08-29-multi-agent-redteam.md for the full plan.
"""

from vibe.redteam.corpus import CorpusEntry, CorpusValidationError, load_corpus
from vibe.redteam.oracles import Finding, Observation
from vibe.redteam.orchestrator import RedTeamOrchestrator
from vibe.redteam.report import build_report, render_json, render_markdown
from vibe.redteam.victim import VictimHarness

__all__ = [
    "CorpusEntry",
    "CorpusValidationError",
    "Finding",
    "Observation",
    "RedTeamOrchestrator",
    "VictimHarness",
    "build_report",
    "load_corpus",
    "render_json",
    "render_markdown",
]
