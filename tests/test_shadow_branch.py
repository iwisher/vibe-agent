"""Tests for ShadowBranchManager (Phase 5.2)."""

import subprocess

import pytest

from vibe.tools.git_shadow import NoOpShadowManager, ShadowBranchManager


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize git repo with cwd=repo (no os.chdir to preserve test isolation)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True
    )

    # Create initial commit
    (repo / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True
    )

    yield repo
    # No teardown needed; tmp_path cleans up automatically


class TestShadowBranchManager:
    """Test shadow branch creation and restoration."""

    def test_create_shadow(self, git_repo):
        """Creating a shadow branch should succeed."""
        manager = ShadowBranchManager(git_repo)
        shadow = manager.create_shadow("sess-001")

        assert shadow is not None
        assert shadow.branch_name == "vibe/shadow-sess-001"
        assert shadow.session_id == "sess-001"
        assert shadow.original_branch == "main" or shadow.original_branch == "master"

    def test_list_shadows(self, git_repo):
        """Listing shadows should return created shadows with metadata."""
        manager = ShadowBranchManager(git_repo)
        manager.create_shadow("sess-001")
        manager.create_shadow("sess-002")

        shadows = manager.list_shadows()
        assert len(shadows) == 2
        assert any(s.session_id == "sess-001" for s in shadows)
        assert any(s.session_id == "sess-002" for s in shadows)

        # Metadata should be populated from git config
        for s in shadows:
            assert s.original_branch in ("main", "master", "HEAD")
            assert s.has_uncommitted_changes is False
            assert s.created_at != ""  # creation timestamp populated from reflog

    def test_restore_shadow(self, git_repo):
        """Restoring a shadow should return workspace to original branch state."""
        manager = ShadowBranchManager(git_repo)

        # Create a file and shadow
        (git_repo / "test.txt").write_text("original")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add test file"], cwd=git_repo, check=True, capture_output=True
        )

        shadow = manager.create_shadow("sess-003")
        assert shadow is not None
        original = shadow.original_branch

        # Modify the file
        (git_repo / "test.txt").write_text("modified")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Modify test file"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        # Restore shadow
        success = manager.restore_shadow("sess-003")
        assert success is True

        # Should be back on the original branch with restored content
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == original
        assert (git_repo / "test.txt").read_text() == "original"

    def test_create_shadow_no_stash_race(self, git_repo):
        """Shadow creation should work even when stash stack is non-empty."""
        manager = ShadowBranchManager(git_repo)

        # Pre-populate the stash stack to simulate a race-prone environment
        (git_repo / "stashed.txt").write_text("pre-existing stash")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "stash", "push", "-m", "unrelated-stash"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        # Now create a shadow — should not be affected by existing stash entries
        shadow = manager.create_shadow("sess-race")
        assert shadow is not None
        assert shadow.branch_name == "vibe/shadow-sess-race"

        # The existing stash should still be present (git stash create is non-destructive)
        stash_list = subprocess.run(
            ["git", "stash", "list"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "unrelated-stash" in stash_list.stdout

    def test_is_write_heavy_operation(self):
        """Write-heavy detection should identify destructive operations."""
        manager = ShadowBranchManager()

        assert manager.is_write_heavy_operation("write_file", {}) is True
        assert manager.is_write_heavy_operation("bash", {"command": "rm -rf /tmp/test"}) is True
        assert manager.is_write_heavy_operation("bash", {"command": "echo hello"}) is False
        assert manager.is_write_heavy_operation("read_file", {}) is False

    def test_write_heavy_false_positives(self):
        """Previously over-broad patterns should not flag benign commands."""
        manager = ShadowBranchManager()

        # These were false positives with the old regex set
        assert manager.is_write_heavy_operation("bash", {"command": "mv --help"}) is False
        assert (
            manager.is_write_heavy_operation("bash", {"command": "python -c \"print('hello')\""})
            is False
        )
        assert manager.is_write_heavy_operation("bash", {"command": "touch file.txt"}) is False
        assert manager.is_write_heavy_operation("bash", {"command": "cp src dst"}) is False
        assert manager.is_write_heavy_operation("bash", {"command": "mkdir -p dir"}) is False

        # Output redirection SHOULD still be flagged as write-heavy
        assert (
            manager.is_write_heavy_operation("bash", {"command": "echo hello > file.txt"}) is True
        )

    def test_no_op_manager(self):
        """NoOpShadowManager should return safe defaults."""
        manager = NoOpShadowManager()

        assert manager.create_shadow("sess") is None
        assert manager.restore_shadow("sess") is False
        assert manager.list_shadows() == []
        assert manager.clean_shadows() == 0
        assert manager.is_write_heavy_operation("bash", {}) is False

    def test_non_git_directory(self, tmp_path):
        """Manager should handle non-git directories gracefully."""
        manager = ShadowBranchManager(tmp_path)

        assert manager._git_available is False
        assert manager.create_shadow("sess") is None
        assert manager.list_shadows() == []

    def test_clean_shadows(self, git_repo):
        """Cleaning old shadows should remove expired branches."""
        manager = ShadowBranchManager(git_repo)
        manager.create_shadow("old-sess")

        # Clean with 0 days should remove all
        removed = manager.clean_shadows(older_than_days=0)
        assert removed >= 1

        shadows = manager.list_shadows()
        assert len(shadows) == 0
