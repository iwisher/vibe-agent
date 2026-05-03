"""Tests for the file safety module."""

import os

import pytest

from vibe.tools.security.file_safety import (
    FileSafetyError,
    FileSafetyGuard,
    check_read_allowed,
    check_write_allowed,
)


class TestWriteDenylist:
    """Test write denylist checks."""

    def test_blocks_ssh_authorized_keys(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_write("~/.ssh/authorized_keys")
        assert exc.value.reason == "write_denylist"

    def test_blocks_etc_passwd(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_write("/etc/passwd")
        assert exc.value.reason == "write_denylist"

    def test_blocks_ssh_prefix(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_write("~/.ssh/config")
        assert exc.value.reason == "write_denylist_prefix"

    def test_blocks_aws_prefix(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_write("~/.aws/credentials")
        assert exc.value.reason == "write_denylist_prefix"

    def test_allows_safe_path(self):
        guard = FileSafetyGuard()
        guard.check_write("/tmp/test.txt")  # Should not raise

    def test_null_byte_injection(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_write("/tmp/test\x00.txt")
        assert exc.value.reason == "null_byte_injection"

    def test_safe_root_restriction(self, tmp_path):
        guard = FileSafetyGuard(safe_root=tmp_path)
        guard.check_write(tmp_path / "test.txt")  # Should not raise
        # Try writing outside safe root (use a path not in denylist)
        with pytest.raises(FileSafetyError) as exc:
            guard.check_write("/tmp/outside_safe_root.txt")
        assert exc.value.reason == "outside_safe_root"


class TestReadBlocklist:
    """Test read blocklist checks."""

    def test_blocks_dev_zero(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_read("/dev/zero")
        assert exc.value.reason == "read_blocklist"

    def test_blocks_dev_urandom(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_read("/dev/urandom")
        assert exc.value.reason == "read_blocklist"

    def test_blocks_etc_prefix(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_read("/etc/hosts")
        assert exc.value.reason == "read_blocklist_prefix"

    def test_blocks_sys_prefix(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_read("/sys/kernel/debug")
        assert exc.value.reason == "read_blocklist_prefix"

    def test_allows_safe_path(self):
        guard = FileSafetyGuard()
        guard.check_read("/tmp/test.txt")  # Should not raise

    def test_null_byte_injection_read(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_read("/tmp/test\x00.txt")
        assert exc.value.reason == "null_byte_injection"

    def test_blocks_index_cache(self):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.check_read("skills/.hub/index-cache")
        assert exc.value.reason == "prompt_injection_defense"


class TestPathTraversal:
    """Test path traversal hardening."""

    def test_valid_path_within_root(self, tmp_path):
        guard = FileSafetyGuard()
        result = guard.validate_within_dir(tmp_path / "test.txt", tmp_path)
        assert result == (tmp_path / "test.txt").resolve()

    def test_traversal_detected(self, tmp_path):
        guard = FileSafetyGuard()
        with pytest.raises(FileSafetyError) as exc:
            guard.validate_within_dir(tmp_path / ".." / "outside.txt", tmp_path)
        assert exc.value.reason == "path_traversal"

    def test_has_traversal_component_true(self):
        guard = FileSafetyGuard()
        assert guard.has_traversal_component("/tmp/../etc/passwd")

    def test_has_traversal_component_false(self):
        guard = FileSafetyGuard()
        assert not guard.has_traversal_component("/tmp/test.txt")

    def test_symlink_escape(self, tmp_path):
        guard = FileSafetyGuard()
        # Create target OUTSIDE tmp_path
        outside = tmp_path.parent / "outside_secret"
        outside.mkdir()
        target_file = outside / "secret.txt"
        target_file.write_text("secret")
        link = tmp_path / "link"
        link.symlink_to(target_file)
        with pytest.raises(FileSafetyError) as exc:
            guard.validate_within_dir(link, tmp_path)
        assert exc.value.reason == "symlink_escape"


class TestReadLoopDetection:
    """Test read loop detection."""

    def test_no_loop_initial_read(self, tmp_path):
        guard = FileSafetyGuard()
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        should_block, reason = guard.check_read_loop(test_file, offset=0, limit=10)
        assert not should_block
        assert reason == ""

    def test_warn_at_3_reads(self, tmp_path):
        guard = FileSafetyGuard()
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        # 3 identical reads
        for _ in range(3):
            should_block, reason = guard.check_read_loop(test_file, offset=0, limit=10)
        assert not should_block
        assert "Warning" in reason

    def test_block_at_4_reads(self, tmp_path):
        guard = FileSafetyGuard()
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        # 4 identical reads
        for _ in range(4):
            should_block, reason = guard.check_read_loop(test_file, offset=0, limit=10)
        assert should_block
        assert "Read loop" in reason

    def test_different_params_reset(self, tmp_path):
        guard = FileSafetyGuard()
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        # 3 reads with different params
        for i in range(3):
            should_block, reason = guard.check_read_loop(test_file, offset=i, limit=10)
            assert not should_block


class TestFileLocking:
    """Test cross-agent file locking."""

    def test_lock_and_unlock(self, tmp_path):
        guard = FileSafetyGuard()
        test_file = tmp_path / "lock_test.txt"
        test_file.write_text("hello")
        fd = guard.lock_path(test_file)
        assert fd >= 0
        guard.unlock_path(fd)

    def test_staleness_check(self, tmp_path):
        guard = FileSafetyGuard()
        test_file = tmp_path / "stale_test.txt"
        test_file.write_text("hello")
        mtime = os.path.getmtime(test_file)
        assert not guard.check_staleness(test_file, mtime)
        # Modify file
        test_file.write_text("modified")
        assert guard.check_staleness(test_file, mtime)


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_check_write_allowed_raises(self):
        with pytest.raises(FileSafetyError):
            check_write_allowed("/etc/passwd")

    def test_check_read_allowed_raises(self):
        with pytest.raises(FileSafetyError):
            check_read_allowed("/dev/zero")
