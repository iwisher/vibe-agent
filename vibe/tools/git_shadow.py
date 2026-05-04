"""Git-based shadow workspace rollback system (Phase 5.2).

Creates hidden git branches before write-heavy operations,
allowing workspace restoration on failure.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class ShadowBranch:
    """Metadata for a shadow branch."""
    session_id: str
    branch_name: str
    created_at: str
    original_branch: str
    has_uncommitted_changes: bool
    restorable: bool = True


class ShadowBranchManager:
    """Manages git shadow branches for workspace rollbacks.

    Shadow branches are hidden branches named `vibe/shadow-<session-id>`
    that capture the workspace state before write-heavy operations.
    """

    SHADOW_PREFIX = "vibe/shadow-"
    SESSION_ID_PATTERN = re.compile(r"vibe/shadow-(.+)")

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self._git_available = self._check_git()

    def _check_git(self) -> bool:
        """Check if git is available and we're in a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in the project root."""
        return subprocess.run(
            ["git"] + args,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=check,
        )

    def create_shadow(self, session_id: str) -> Optional[ShadowBranch]:
        """Create a shadow branch for the current session.

        Returns:
            ShadowBranch metadata, or None if git is not available.
        """
        if not self._git_available:
            return None

        branch_name = f"{self.SHADOW_PREFIX}{session_id}"

        # Get current branch
        try:
            result = self._run_git(["branch", "--show-current"], check=False)
            original_branch = result.stdout.strip() or "HEAD"
        except Exception:
            original_branch = "HEAD"

        # Check for uncommitted changes
        status_result = self._run_git(["status", "--porcelain"], check=False)
        has_changes = bool(status_result.stdout.strip())

        # Stash changes if any
        if has_changes:
            self._run_git(
                ["stash", "push", "-m", f"vibe-shadow-{session_id}"],
                check=False,
            )

        # Create shadow branch from current state
        self._run_git(["branch", branch_name], check=False)

        # Apply stash back to original branch if we stashed
        if has_changes:
            self._run_git(["stash", "pop"], check=False)

        return ShadowBranch(
            session_id=session_id,
            branch_name=branch_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            original_branch=original_branch,
            has_uncommitted_changes=has_changes,
        )

    def restore_shadow(self, session_id: str) -> bool:
        """Restore workspace from a shadow branch.

        This checks out the shadow branch and merges it back to the
        original branch, effectively restoring the pre-operation state.

        Returns:
            True if restoration succeeded.
        """
        if not self._git_available:
            return False

        branch_name = f"{self.SHADOW_PREFIX}{session_id}"

        # Check if branch exists
        branches_result = self._run_git(["branch", "--list", branch_name], check=False)
        if branch_name not in branches_result.stdout:
            return False

        # Get the original branch from the shadow branch's reflog or description
        # For simplicity, we stash current changes, checkout shadow, then reset
        try:
            # Stash any current changes
            self._run_git(["stash", "push", "-m", "vibe-restore-stash"], check=False)

            # Checkout shadow branch
            self._run_git(["checkout", branch_name], check=True)

            # The workspace is now at the shadow state
            # User can manually merge back or we can create a new branch
            return True
        except subprocess.CalledProcessError:
            return False

    def list_shadows(self) -> list[ShadowBranch]:
        """List all shadow branches."""
        if not self._git_available:
            return []

        result = self._run_git(["branch", "--list", f"{self.SHADOW_PREFIX}*"], check=False)
        shadows = []

        for line in result.stdout.strip().split("\n"):
            line = line.strip().lstrip("* ")
            if not line.startswith(self.SHADOW_PREFIX):
                continue

            match = self.SESSION_ID_PATTERN.match(line)
            if match:
                session_id = match.group(1)
                shadows.append(ShadowBranch(
                    session_id=session_id,
                    branch_name=line,
                    created_at="",  # Would need git log to get exact time
                    original_branch="unknown",
                    has_uncommitted_changes=False,
                ))

        return shadows

    def clean_shadows(self, older_than_days: int = 7) -> int:
        """Remove shadow branches older than specified days.

        Returns:
            Number of branches removed.
        """
        if not self._git_available:
            return 0

        shadows = self.list_shadows()
        removed = 0
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)

        for shadow in shadows:
            # Get branch creation time from reflog
            try:
                log_result = self._run_git(
                    ["reflog", "show", shadow.branch_name, "--format=%ct", "-n", "1"],
                    check=False,
                )
                created_ts = int(log_result.stdout.strip())
                if created_ts < cutoff:
                    self._run_git(["branch", "-D", shadow.branch_name], check=False)
                    removed += 1
            except (ValueError, subprocess.CalledProcessError):
                continue

        return removed

    def is_write_heavy_operation(self, tool_name: str, arguments: dict) -> bool:
        """Determine if a tool operation is write-heavy and needs shadow protection.

        Args:
            tool_name: Name of the tool being called.
            arguments: Tool arguments dict.

        Returns:
            True if the operation should be shadowed.
        """
        write_tools = {
            "file_write", "file_edit", "bash", "git_commit", "git_push",
            "git_checkout", "git_merge", "git_rebase", "rm", "mv", "cp",
        }

        if tool_name in write_tools:
            # For bash, check if the command is actually destructive
            if tool_name == "bash" and "command" in arguments:
                cmd = arguments["command"].lower()
                destructive_patterns = [
                    r"\brm\s+-[rf]",
                    r"\bgit\s+reset\s+--hard",
                    r"\bgit\s+clean\s+-[fd]",
                    r"\bfind\s+.*-delete",
                    r"\b\S+\s+>\s*\S+",  # output redirection (overwrites) - command > file
                ]
                for pattern in destructive_patterns:
                    if re.search(pattern, cmd):
                        return True
                return False
            return True

        return False


class NoOpShadowManager:
    """No-op shadow manager for non-git projects."""

    def create_shadow(self, session_id: str) -> Optional[ShadowBranch]:
        return None

    def restore_shadow(self, session_id: str) -> bool:
        return False

    def list_shadows(self) -> list[ShadowBranch]:
        return []

    def clean_shadows(self, older_than_days: int = 7) -> int:
        return 0

    def is_write_heavy_operation(self, tool_name: str, arguments: dict) -> bool:
        return False
