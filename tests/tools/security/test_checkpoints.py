"""Tests for CheckpointManager rollback capability."""

import os

from vibe.tools.security.checkpoints import (
    CheckpointManager,
    CheckpointType,
)


class TestCheckpointManager:
    """Test checkpoint creation and rollback."""

    def test_create_checkpoint(self, tmp_path):
        """Creating a checkpoint should capture file state."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("original content")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Before editing test.txt",
            files=[str(test_file)],
        )

        assert cp.id.startswith("cp_")
        assert cp.type == "file_operation"
        assert len(cp.file_states) == 1
        assert cp.file_states[0].path == str(test_file)
        assert cp.file_states[0].exists is True
        assert cp.file_states[0].content == "original content"

    def test_rollback_file(self, tmp_path):
        """Rolling back should restore file to original state."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        # Create and checkpoint a file
        test_file = tmp_path / "test.txt"
        test_file.write_text("original content")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Before editing",
            files=[str(test_file)],
        )

        # Modify the file
        test_file.write_text("modified content")

        # Rollback
        success = manager.rollback(cp.id)
        assert success is True

        # File should be restored
        assert test_file.read_text() == "original content"

    def test_rollback_nonexistent_file(self, tmp_path):
        """Rolling back should remove file that didn't exist at checkpoint."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        # Checkpoint when file doesn't exist
        test_file = tmp_path / "new_file.txt"

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Before creating file",
            files=[str(test_file)],
        )

        # Create the file
        test_file.write_text("new content")
        assert test_file.exists()

        # Rollback
        success = manager.rollback(cp.id)
        assert success is True

        # File should be removed
        assert not test_file.exists()

    def test_rollback_missing_checkpoint(self, tmp_path):
        """Rolling back non-existent checkpoint should return False."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")
        success = manager.rollback("nonexistent_id")
        assert success is False

    def test_delete_checkpoint(self, tmp_path):
        """Deleting checkpoint should clean up backups."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Test",
            files=[str(test_file)],
        )

        backup_path = cp.file_states[0].backup_path
        assert os.path.exists(backup_path)

        # Delete checkpoint
        success = manager.delete(cp.id)
        assert success is True

        # Backup should be removed
        assert not os.path.exists(backup_path)

        # Checkpoint should no longer be tracked
        assert manager.get(cp.id) is None

    def test_list_checkpoints(self, tmp_path):
        """Listing checkpoints should return all active ones."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        cp1 = manager.create(CheckpointType.FILE_OPERATION, "First")
        cp2 = manager.create(CheckpointType.ENV_CHANGE, "Second")

        checkpoints = manager.list_checkpoints()
        assert len(checkpoints) == 2
        assert checkpoints[0].id == cp1.id
        assert checkpoints[1].id == cp2.id

    def test_get_checkpoint(self, tmp_path):
        """Getting a specific checkpoint by ID."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        cp = manager.create(CheckpointType.FILE_OPERATION, "Test")
        retrieved = manager.get(cp.id)

        assert retrieved is not None
        assert retrieved.id == cp.id
        assert retrieved.description == "Test"

    def test_env_state_capture(self, tmp_path):
        """Capturing environment variables."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        # Set a test env var
        os.environ["VIBE_TEST_VAR"] = "test_value"

        cp = manager.create(
            CheckpointType.ENV_CHANGE,
            "Before env change",
            env_vars=["VIBE_TEST_VAR"],
        )

        assert cp.env_state is not None
        assert "VIBE_TEST_VAR" in cp.env_state.added
        assert cp.env_state.added["VIBE_TEST_VAR"] == "test_value"

        # Clean up
        del os.environ["VIBE_TEST_VAR"]

    def test_env_state_rollback(self, tmp_path):
        """Rolling back should restore env vars."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        # Set and checkpoint
        os.environ["VIBE_TEST_VAR"] = "original"

        cp = manager.create(
            CheckpointType.ENV_CHANGE,
            "Before env change",
            env_vars=["VIBE_TEST_VAR"],
        )

        # Modify env var
        os.environ["VIBE_TEST_VAR"] = "modified"

        # Rollback
        manager.rollback(cp.id)

        # Env var should be removed (since we captured it as "added")
        assert "VIBE_TEST_VAR" not in os.environ

    def test_cwd_capture(self, tmp_path):
        """Checkpoint should capture current working directory."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        original_cwd = os.getcwd()
        cp = manager.create(CheckpointType.DIRECTORY_CHANGE, "Test")

        assert cp.cwd == original_cwd

    def test_ttl_expiration(self, tmp_path):
        """Checkpoints with TTL should expire."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Short lived",
            ttl_seconds=0,  # Expire immediately
        )

        # Should be expired on next list
        checkpoints = manager.list_checkpoints()
        assert len(checkpoints) == 0
        assert manager.get(cp.id) is None

    def test_stats(self, tmp_path):
        """Stats should report checkpoint count and size."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "test.txt"
        test_file.write_text("some content here")

        manager.create(
            CheckpointType.FILE_OPERATION,
            "Test",
            files=[str(test_file)],
        )

        stats = manager.stats()
        assert stats["count"] == 1
        assert stats["total_backup_size"] > 0
        assert "backups" in stats["backup_dir"]

    def test_clear_all(self, tmp_path):
        """Clear all should remove everything."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Test",
            files=[str(test_file)],
        )

        assert len(manager.list_checkpoints()) == 1

        manager.clear_all()

        assert len(manager.list_checkpoints()) == 0
        assert manager.get(cp.id) is None

    def test_directory_backup(self, tmp_path):
        """Checkpoint should handle directories."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file1.txt").write_text("content1")
        (test_dir / "file2.txt").write_text("content2")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Before dir change",
            files=[str(test_dir)],
        )

        assert cp.file_states[0].exists is True
        assert cp.file_states[0].backup_path is not None

        # Modify directory
        (test_dir / "file3.txt").write_text("content3")

        # Rollback
        manager.rollback(cp.id)

        # Directory should be restored without file3
        assert (test_dir / "file1.txt").exists()
        assert (test_dir / "file2.txt").exists()
        assert not (test_dir / "file3.txt").exists()

    def test_multiple_files(self, tmp_path):
        """Checkpoint multiple files at once."""
        manager = CheckpointManager(backup_dir=tmp_path / "backups")

        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content1")
        file2.write_text("content2")

        cp = manager.create(
            CheckpointType.FILE_OPERATION,
            "Before batch edit",
            files=[str(file1), str(file2)],
        )

        assert len(cp.file_states) == 2

        # Modify both
        file1.write_text("modified1")
        file2.write_text("modified2")

        # Rollback
        manager.rollback(cp.id)

        assert file1.read_text() == "content1"
        assert file2.read_text() == "content2"
