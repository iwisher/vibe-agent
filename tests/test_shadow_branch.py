"""Tests for ShadowBranchManager (Phase 5.2)."""

import os
import subprocess
from pathlib import Path

import pytest

from vibe.tools.git_shadow import ShadowBranchManager, NoOpShadowManager


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    os.chdir(repo)

    # Initialize git repo
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    yield repo

    os.chdir(Path.cwd().parent)


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
        """Listing shadows should return created shadows."""
        manager = ShadowBranchManager(git_repo)
        manager.create_shadow("sess-001")
        manager.create_shadow("sess-002")

        shadows = manager.list_shadows()
        assert len(shadows) == 2
        assert any(s.session_id == "sess-001" for s in shadows)
        assert any(s.session_id == "sess-002" for s in shadows)

    def test_restore_shadow(self, git_repo):
        """Restoring a shadow should checkout the shadow branch."""
        manager = ShadowBranchManager(git_repo)

        # Create a file and shadow
        (git_repo / "test.txt").write_text("original")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, capture_output=True)

        shadow = manager.create_shadow("sess-003")
        assert shadow is not None

        # Modify the file
        (git_repo / "test.txt").write_text("modified")
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify test file"], check=True, capture_output=True)

        # Restore shadow
        success = manager.restore_shadow("sess-003")
        assert success is True

        # Should be on shadow branch
        result = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, check=True)
        assert "vibe/shadow-sess-003" in result.stdout

    def test_is_write_heavy_operation(self):
        """Write-heavy detection should identify destructive operations."""
        manager = ShadowBranchManager()

        assert manager.is_write_heavy_operation("file_write", {}) is True
        assert manager.is_write_heavy_operation("bash", {"command": "rm -rf /tmp/test"}) is True
        assert manager.is_write_heavy_operation("bash", {"command": "echo hello"}) is False
        assert manager.is_write_heavy_operation("file_read", {}) is False

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
        os.chdir(tmp_path)
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
