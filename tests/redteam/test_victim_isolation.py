"""Victim harness isolation self-test (plan workplan step 2).

Must prove — before any destructive payload ever runs — that the harness jails
writes to a fresh tmpdir and that shadow branches created mid-run are cleaned up.
"""

import subprocess
from pathlib import Path

import pytest

from vibe.redteam.victim import SHADOW_BRANCH_PREFIX, VictimHarness


def test_safe_root_jailed_to_fresh_tmpdir():
    with VictimHarness() as victim:
        assert victim.workdir is not None and victim.workdir.is_dir()
        assert victim.security_config is not None
        safe_root = victim.security_config.file_safety.safe_root
        assert safe_root == str(victim.workdir)
        assert "vibe-redteam-victim-" in safe_root
        workdir = victim.workdir
    # tmpdir removed on exit
    assert not workdir.exists()


def test_security_coordinator_uses_jail():
    with VictimHarness() as victim:
        coordinator = victim.build_security_coordinator()
        assert coordinator._file_guard is not None
        assert Path(coordinator._file_guard.safe_root) == victim.workdir


def test_shadow_branch_cleanup(tmp_path):
    """Create a throwaway git repo, leak a shadow branch, assert teardown removes it."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    leaked = f"{SHADOW_BRANCH_PREFIX}redteam-selftest"

    with pytest.raises(RuntimeError, match="leaked"):
        with VictimHarness(repo_root=tmp_path) as victim:
            victim._git("branch", leaked)
            assert leaked in victim._shadow_branches()

    # Cleaned up by __exit__ despite raising
    out = subprocess.run(
        ["git", "branch", "--list", f"{SHADOW_BRANCH_PREFIX}*"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert leaked not in out.stdout


def test_no_shadow_branch_no_error(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    with VictimHarness(repo_root=tmp_path):
        pass  # no leak, no error


def test_other_sessions_shadow_branches_never_deleted(tmp_path):
    """A vibe/shadow-* branch that exists before the run belongs to another session."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    foreign = f"{SHADOW_BRANCH_PREFIX}other-session"
    subprocess.run(["git", "branch", foreign], cwd=tmp_path, check=True, capture_output=True)

    with VictimHarness(repo_root=tmp_path):
        pass  # run without creating branches

    out = subprocess.run(
        ["git", "branch", "--list", f"{SHADOW_BRANCH_PREFIX}*"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert foreign in out.stdout


def test_real_repo_untouched():
    """The harness must not create shadow branches in the actual repository."""
    repo = Path(__file__).resolve().parents[2]
    with VictimHarness(repo_root=repo) as victim:
        before = victim._branches_before
    out = subprocess.run(
        ["git", "branch", "--list", f"{SHADOW_BRANCH_PREFIX}*"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    current = {
        line.strip().lstrip("* ").strip() for line in out.stdout.splitlines() if line.strip()
    }
    assert current == before
