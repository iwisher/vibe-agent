"""Multi-agent coordination module for Claude Code Clone.

This module provides team-based multi-agent coordination with isolated workers,
result synthesis, and independent verification.
"""

from .team_coordinator import TeamCoordinator, TeamConfig, WorkerTask, TeamResult
from .worker import Worker, WorkerState, WorkerResult
from .synthesis import ResultSynthesizer, SynthesisStrategy
from .verification import ResultVerifier, VerificationResult

__all__ = [
    # Team coordinator
    "TeamCoordinator",
    "TeamConfig",
    "WorkerTask",
    "TeamResult",
    # Worker
    "Worker",
    "WorkerState",
    "WorkerResult",
    # Synthesis
    "ResultSynthesizer",
    "SynthesisStrategy",
    # Verification
    "ResultVerifier",
    "VerificationResult",
]
