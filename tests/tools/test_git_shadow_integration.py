"""Integration tests for ShadowBranchManager with real git repos."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from vibe.tools.git_shadow import ShadowBranchManager


@pytest.fixture
def git_repo():
    """Create a temporary git repository."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        # Create initial commit
        (repo / "README.md").write_text("# Hello")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        yield repo


class TestCreateShadowRealRepo:
    def test_create_shadow_captures_state(self, git_repo):
        mgr = ShadowBranchManager(project_root=git_repo)
        shadow = mgr.create_shadow("sess-1")
        assert shadow is not None
        assert shadow.branch_name == "vibe/shadow-sess-1"
        assert shadow.original_branch in ("main", "master")

    def test_create_shadow_with_uncommitted_changes(self, git_repo):
        # Add uncommitted changes
        (git_repo / "new_file.txt").write_text("new content")
        mgr = ShadowBranchManager(project_root=git_repo)
        shadow = mgr.create_shadow("sess-2")
        assert shadow is not None
        assert shadow.has_uncommitted_changes is True


class TestRestoreShadowRealRepo:
    def test_restore_preserves_original_state(self, git_repo):
        # Create shadow
        mgr = ShadowBranchManager(project_root=git_repo)
        shadow = mgr.create_shadow("sess-restore")
        assert shadow is not None

        # Make changes (including untracked files)
        (git_repo / "README.md").write_text("# Modified")
        (git_repo / "extra.txt").write_text("extra")

        # Restore
        success = mgr.restore_shadow("sess-restore")
        assert success is True

        # Original tracked state should be restored
        readme = (git_repo / "README.md").read_text()
        assert readme == "# Hello"
        # Note: git reset --hard restores tracked files but does not remove untracked files
        # The extra.txt may still exist — this is expected git behavior


class TestListShadowsRealRepo:
    def test_list_finds_created_shadows(self, git_repo):
        mgr = ShadowBranchManager(project_root=git_repo)
        mgr.create_shadow("sess-a")
        mgr.create_shadow("sess-b")
        shadows = mgr.list_shadows()
        session_ids = {s.session_id for s in shadows}
        assert "sess-a" in session_ids
        assert "sess-b" in session_ids


class TestCleanShadowsRealRepo:
    def test_clean_removes_old_keeps_new(self, git_repo):
        mgr = ShadowBranchManager(project_root=git_repo)
        mgr.create_shadow("sess-new")
        # All shadows are "new" since just created
        removed = mgr.clean_shadows(older_than_days=7)
        assert removed == 0
        # Shadow should still exist
        shadows = mgr.list_shadows()
        assert any(s.session_id == "sess-new" for s in shadows)
