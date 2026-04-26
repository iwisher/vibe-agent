"""Tests for the durable approval store."""

import os
import stat
from pathlib import Path

import pytest

from vibe.tools.security.approval_store import ApprovalStore, ApprovalEntry


class TestApprovalStore:
    """Test ApprovalStore."""

    def test_create_store(self, tmp_path):
        """Store creates file with correct permissions."""
        store_path = tmp_path / "approvals.json"
        store = ApprovalStore(store_path=store_path)
        assert store_path.exists()
        mode = store_path.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_create_parent_dir(self, tmp_path):
        """Parent directory created with 0o700."""
        store_path = tmp_path / "subdir" / "approvals.json"
        store = ApprovalStore(store_path=store_path)
        assert store_path.parent.exists()
        mode = store_path.parent.stat().st_mode
        assert stat.S_IMODE(mode) == 0o700

    def test_add_and_check_approval(self, tmp_path):
        """Can add and check approvals."""
        store = ApprovalStore(store_path=tmp_path / "approvals.json")
        entry = ApprovalEntry(
            approval_type="pattern",
            key="test-pattern",
            timestamp="2024-01-01T00:00:00",
            approved_by="user",
        )
        store.add_approval(entry)
        assert store.is_approved("pattern", "test-pattern")

    def test_duplicate_approval_ignored(self, tmp_path):
        """Duplicate approvals are silently ignored."""
        store = ApprovalStore(store_path=tmp_path / "approvals.json")
        entry = ApprovalEntry(
            approval_type="pattern",
            key="test-pattern",
            timestamp="2024-01-01T00:00:00",
            approved_by="user",
        )
        store.add_approval(entry)
        store.add_approval(entry)
        approvals = store.list_approvals()
        assert len(approvals) == 1

    def test_remove_approval(self, tmp_path):
        """Can remove approvals."""
        store = ApprovalStore(store_path=tmp_path / "approvals.json")
        entry = ApprovalEntry(
            approval_type="pattern",
            key="test-pattern",
            timestamp="2024-01-01T00:00:00",
            approved_by="user",
        )
        store.add_approval(entry)
        assert store.is_approved("pattern", "test-pattern")
        assert store.remove_approval("pattern", "test-pattern")
        assert not store.is_approved("pattern", "test-pattern")

    def test_remove_nonexistent(self, tmp_path):
        """Removing nonexistent approval returns False."""
        store = ApprovalStore(store_path=tmp_path / "approvals.json")
        assert not store.remove_approval("pattern", "nonexistent")

    def test_clear(self, tmp_path):
        """Can clear all approvals."""
        store = ApprovalStore(store_path=tmp_path / "approvals.json")
        entry = ApprovalEntry(
            approval_type="pattern",
            key="test-pattern",
            timestamp="2024-01-01T00:00:00",
            approved_by="user",
        )
        store.add_approval(entry)
        store.clear()
        assert not store.is_approved("pattern", "test-pattern")
        assert len(store.list_approvals()) == 0

    def test_command_hash(self):
        """Command hashing works."""
        hash1 = ApprovalStore.hash_command("ls -la")
        hash2 = ApprovalStore.hash_command("ls -la")
        hash3 = ApprovalStore.hash_command("ls -lb")
        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 64  # SHA-256 hex

    def test_symlink_rejection(self, tmp_path):
        """Symlink in path raises PermissionError."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)
        store_path = link_dir / "approvals.json"
        with pytest.raises(PermissionError):
            ApprovalStore(store_path=store_path)
