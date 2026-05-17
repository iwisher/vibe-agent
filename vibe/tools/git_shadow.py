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


def _sanitize_session_id(session_id: str) -> str:
    """Sanitize session_id for safe use in git branch names.

    Only allows alphanumeric characters, hyphens, and underscores.
    Replaces everything else with underscores.
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)


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
        """Create a shadow branch for the current workspace state.

        Uses `git stash create` to capture uncommitted changes without
        modifying the stash stack, avoiding race conditions.

        Args:
            session_id: Session identifier to associate with the shadow.

        Returns:
            ShadowBranch metadata, or None if git is not available.
        """
        if not self._git_available:
            return None

        safe_session_id = _sanitize_session_id(session_id)
        branch_name = f"vibe/shadow-{safe_session_id}"
        timestamp = datetime.now(timezone.utc).isoformat()

        # Get current branch
        result = self._run_git(["branch", "--show-current"], check=False)
        original_branch = result.stdout.strip() if result.returncode == 0 else "HEAD"

        # Check for uncommitted changes
        status_result = self._run_git(["status", "--porcelain"], check=False)
        has_changes = bool(status_result.stdout.strip())

        # Use `git stash create` to get a commit object without touching the stash stack
        stash_commit = None
        if has_changes:
            create_result = self._run_git(
                ["stash", "create", f"vibe-shadow-{safe_session_id}"],
                check=False,
            )
            if create_result.returncode == 0 and create_result.stdout.strip():
                stash_commit = create_result.stdout.strip()

        # Create shadow branch from current HEAD or stash commit
        if stash_commit:
            self._run_git(["branch", branch_name, stash_commit], check=False)
        else:
            self._run_git(["branch", branch_name], check=False)

        # Store metadata in git config for retrieval by list_shadows / restore_shadow
        self._run_git(
            ["config", f"branch.{branch_name}.vibe-original-branch", original_branch],
            check=False,
        )
        self._run_git(
            ["config", f"branch.{branch_name}.vibe-has-changes", str(has_changes).lower()],
            check=False,
        )

        return ShadowBranch(
            session_id=session_id,
            branch_name=branch_name,
            created_at=timestamp,
            original_branch=original_branch,
            has_uncommitted_changes=has_changes,
            restorable=True,
        )

    def restore_shadow(self, session_id: str) -> bool:
        """Restore workspace from a shadow branch.

        Checks out the shadow branch, then returns to the original branch
        and resets it to the shadow state, effectively restoring the
        pre-operation state while keeping the user on their original branch.

        Returns:
            True if restoration succeeded.
        """
        if not self._git_available:
            return False

        safe_session_id = _sanitize_session_id(session_id)
        branch_name = f"{self.SHADOW_PREFIX}{safe_session_id}"

        # Check if branch exists
        branches_result = self._run_git(["branch", "--list", branch_name], check=False)
        if branch_name not in branches_result.stdout:
            return False

        # Read original branch from git config stored during create_shadow
        config_result = self._run_git(
            ["config", f"branch.{branch_name}.vibe-original-branch"],
            check=False,
        )
        original_branch = config_result.stdout.strip() if config_result.returncode == 0 else ""

        try:
            # Stash any current changes on the current branch before switching
            stashed = False
            stash_res = self._run_git(["stash", "push", "-m", "vibe-restore-stash"], check=False)
            if stash_res.returncode == 0 and "No local changes to save" not in stash_res.stderr:
                stashed = True

            # Checkout shadow branch to get its state
            self._run_git(["checkout", branch_name], check=True)

            # If we know the original branch, switch back to it and reset to shadow's state
            if original_branch and original_branch != branch_name:
                self._run_git(["checkout", original_branch], check=True)
                self._run_git(["reset", "--hard", branch_name], check=True)

            return True
        except subprocess.CalledProcessError:
            # Rollback the stash if we failed to restore
            if stashed:
                self._run_git(["stash", "pop"], check=False)
            return False

    def list_shadows(self) -> list[ShadowBranch]:
        """List all shadow branches with metadata retrieved from git config."""
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

                # Retrieve metadata stored via git config during create_shadow
                orig_result = self._run_git(
                    ["config", f"branch.{line}.vibe-original-branch"],
                    check=False,
                )
                original_branch = (
                    orig_result.stdout.strip() if orig_result.returncode == 0 else "unknown"
                )

                changes_result = self._run_git(
                    ["config", f"branch.{line}.vibe-has-changes"],
                    check=False,
                )
                has_changes = (
                    changes_result.stdout.strip() == "true"
                    if changes_result.returncode == 0
                    else False
                )

                # Get creation time from reflog
                created_at = ""
                log_result = self._run_git(
                    ["reflog", "show", line, "--format=%ct", "-n", "1"],
                    check=False,
                )
                if log_result.returncode == 0 and log_result.stdout.strip():
                    try:
                        from datetime import datetime, timezone

                        created_at = datetime.fromtimestamp(
                            int(log_result.stdout.strip()), tz=timezone.utc
                        ).isoformat()
                    except (ValueError, OSError):
                        pass

                shadows.append(
                    ShadowBranch(
                        session_id=session_id,
                        branch_name=line,
                        created_at=created_at,
                        original_branch=original_branch,
                        has_uncommitted_changes=has_changes,
                    )
                )

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
            "write_file",
            "delete_file",
            "edit_file",
            "bash",
            "shell",
            "execute",
            "git_commit",
            "git_push",
            "git_checkout",
            "git_merge",
            "git_rebase",
            "rm",
            "mv",
            "cp",
        }

        if tool_name in write_tools:
            # For bash, check if the command is actually destructive
            if tool_name == "bash" and "command" in arguments:
                cmd = arguments["command"].lower()
                destructive_patterns = [
                    # Destructive removal
                    r"\brm\s+-[rf]",
                    r"\bfind\s+.*-delete",
                    # Destructive git operations
                    r"\bgit\s+reset\s+--hard",
                    r"\bgit\s+clean\s+-[fd]",
                    # Shell output redirection (overwrites or appends)
                    r"\s[>]{1,2}\s*\S+",
                    # Dangerous permission changes
                    r"\bchmod\s+.*777",
                    r"\bchown\s+",
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
