"""Isolated victim harness for red-team runs.

Hard prerequisite (plan workplan step 2): every run jails
``SecurityConfig.file_safety.safe_root`` to a fresh
``tempfile.TemporaryDirectory``, and teardown deletes any ``vibe/shadow-*`` git
branches created while the harness was open. Only branches provably created
during this run are touched — branches from other live sessions are left alone.
"""

import subprocess
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any

from vibe.core.config import SecurityConfig

SHADOW_BRANCH_PREFIX = "vibe/shadow-"


class VictimHarness:
    """Context manager building an isolated victim security stack.

    Usage::

        with VictimHarness() as victim:
            victim.security_config  # safe_root jailed to the tmpdir
            victim.workdir          # the isolated working directory
    """

    def __init__(self, repo_root: Path | None = None, approval_mode: str = "strict") -> None:
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.approval_mode = approval_mode
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.workdir: Path | None = None
        self.security_config: SecurityConfig | None = None
        self._branches_before: set[str] = set()

    def __enter__(self) -> "VictimHarness":
        self._tmp = tempfile.TemporaryDirectory(prefix="vibe-redteam-victim-")
        self.workdir = Path(self._tmp.name).resolve()
        self._branches_before = self._shadow_branches()

        config = SecurityConfig(approval_mode=self.approval_mode)
        config.file_safety.safe_root = str(self.workdir)
        self.security_config = config
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        leaked = self.cleanup_shadow_branches()
        if self._tmp is not None:
            self._tmp.cleanup()
        if leaked and exc_type is None:
            raise RuntimeError(
                f"shadow branches leaked from red-team run: {sorted(leaked)} (cleaned up)"
            )

    def build_security_coordinator(self, llm_client: Any | None = None) -> Any:
        """Create a real SecurityCoordinator jailed to this harness's tmpdir."""
        from vibe.core.coordinators import SecurityCoordinator

        assert self.security_config is not None, "harness not entered"
        return SecurityCoordinator(config=self.security_config, llm_client=llm_client)

    # ── Shadow-branch isolation ───────────────────────────────────────────────

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:  # git not installed — no branches to leak
            return ""
        return proc.stdout if proc.returncode == 0 else ""

    def _shadow_branches(self) -> set[str]:
        """Shadow branch names (for-each-ref: no decoration prefixes to strip)."""
        out = self._git(
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/heads/{SHADOW_BRANCH_PREFIX}*",
        )
        return {line.strip() for line in out.splitlines() if line.strip()}

    def cleanup_shadow_branches(self) -> set[str]:
        """Delete shadow branches created during this run; return the leaked set.

        Only branches absent at __enter__ are candidates — pre-existing branches
        belonging to other live sessions are never deleted. Residual limitation:
        a branch created by a *concurrent* session mid-run is indistinguishable
        from one this run created and would be collected.
        """
        leaked = self._shadow_branches() - self._branches_before
        for branch in leaked:
            self._git("branch", "-D", branch)
        # Verify deletions actually landed; report only what was cleaned.
        remaining = self._shadow_branches()
        return leaked - remaining
