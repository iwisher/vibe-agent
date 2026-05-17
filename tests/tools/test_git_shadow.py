"""Unit tests for ShadowBranchManager (mocked subprocess)."""

from unittest.mock import MagicMock, patch

import pytest

from vibe.tools.git_shadow import (
    NoOpShadowManager,
    ShadowBranch,
    ShadowBranchManager,
    _sanitize_session_id,
)


class TestSanitizeSessionId:
    def test_safe_chars_preserved(self):
        assert _sanitize_session_id("abc-123_test") == "abc-123_test"

    def test_special_chars_replaced(self):
        assert _sanitize_session_id("abc/123@test#") == "abc_123_test_"

    def test_empty_string(self):
        assert _sanitize_session_id("") == ""


class TestShadowBranchManagerInit:
    def test_git_available(self):
        with patch(
            "vibe.tools.git_shadow.subprocess.run",
            return_value=MagicMock(returncode=0),
        ):
            mgr = ShadowBranchManager()
            assert mgr._git_available is True

    def test_git_not_available(self):
        with patch(
            "vibe.tools.git_shadow.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            mgr = ShadowBranchManager()
            assert mgr._git_available is False


class TestCreateShadow:
    def test_no_git_returns_none(self):
        mgr = ShadowBranchManager()
        mgr._git_available = False
        assert mgr.create_shadow("sess-1") is None

    def test_create_shadow_success(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[1] == "branch" and args[2] == "--show-current":
                result.stdout = "main\n"
            elif args[1] == "status" and args[2] == "--porcelain":
                result.stdout = " M file.txt\n"
            elif args[1] == "stash" and args[2] == "create":
                result.stdout = "abc1234\n"
            elif args[1] == "config":
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            shadow = mgr.create_shadow("sess-1")
            assert shadow is not None
            assert shadow.session_id == "sess-1"
            assert shadow.branch_name == "vibe/shadow-sess-1"
            assert shadow.original_branch == "main"
            assert shadow.has_uncommitted_changes is True

    def test_create_shadow_no_changes(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[1] == "branch" and args[2] == "--show-current":
                result.stdout = "feature\n"
            elif args[1] == "status" and args[2] == "--porcelain":
                result.stdout = ""
            elif args[1] == "config":
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            shadow = mgr.create_shadow("sess-2")
            assert shadow is not None
            assert shadow.original_branch == "feature"
            assert shadow.has_uncommitted_changes is False


class TestListShadows:
    def test_empty(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[1] == "branch" and args[2] == "--list":
                result.stdout = ""
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            assert mgr.list_shadows() == []

    def test_list_with_metadata(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            if args[1] == "branch" and args[2] == "--list":
                result.stdout = "  vibe/shadow-sess-abc\n"
            elif args[1] == "config" and "vibe-original-branch" in " ".join(args):
                result.stdout = "main\n"
            elif args[1] == "config" and "vibe-has-changes" in " ".join(args):
                result.stdout = "true\n"
            elif args[1] == "reflog":
                result.stdout = "1715904000\n"
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            shadows = mgr.list_shadows()
            assert len(shadows) == 1
            assert shadows[0].session_id == "sess-abc"
            assert shadows[0].original_branch == "main"
            assert shadows[0].has_uncommitted_changes is True


class TestRestoreShadow:
    def test_not_found(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[1] == "branch" and args[2] == "--list":
                result.stdout = ""
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            assert mgr.restore_shadow("missing") is False

    def test_restore_success(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        calls = []

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            calls.append(args)
            if args[1] == "branch" and args[2] == "--list":
                result.stdout = "  vibe/shadow-sess-1\n"
            elif args[1] == "config" and "vibe-original-branch" in args:
                result.stdout = "main\n"
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            assert mgr.restore_shadow("sess-1") is True
            # Should have checked branch, read config, stashed, checked out shadow, checked out main, reset
            assert any("checkout" in c for c in calls)
            assert any("reset" in c for c in calls)


class TestCleanShadows:
    def test_removes_old(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[1] == "branch" and args[2] == "--list":
                result.stdout = "  vibe/shadow-old\n"
            elif args[1] == "reflog":
                # Very old timestamp
                result.stdout = "1000000000\n"
            elif args[1] == "branch" and args[2] == "-D":
                result.stdout = "Deleted\n"
            else:
                result.stdout = ""
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            removed = mgr.clean_shadows(older_than_days=1)
            assert removed == 1

    def test_keeps_recent(self):
        mgr = ShadowBranchManager()
        mgr._git_available = True

        import time

        now_ts = int(time.time())

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if args[1] == "branch" and args[2] == "--list":
                result.stdout = "  vibe/shadow-recent\n"
            elif args[1] == "reflog":
                result.stdout = f"{now_ts}\n"
            else:
                result.stdout = ""
            return result

        with patch("vibe.tools.git_shadow.subprocess.run", side_effect=mock_run):
            removed = mgr.clean_shadows(older_than_days=7)
            assert removed == 0


class TestIsWriteHeavy:
    def test_bash_destructive(self):
        mgr = ShadowBranchManager()
        assert mgr.is_write_heavy_operation("bash", {"command": "rm -rf /tmp"}) is True

    def test_bash_safe(self):
        mgr = ShadowBranchManager()
        assert mgr.is_write_heavy_operation("bash", {"command": "echo hello"}) is False

    def test_file_write(self):
        mgr = ShadowBranchManager()
        assert mgr.is_write_heavy_operation("write_file", {"path": "/tmp/test"}) is True

    def test_file_read(self):
        mgr = ShadowBranchManager()
        assert mgr.is_write_heavy_operation("file_read", {"path": "/tmp/test"}) is False

    def test_git_commit(self):
        mgr = ShadowBranchManager()
        assert mgr.is_write_heavy_operation("git_commit", {"message": "test"}) is True

    def test_redirection_detected(self):
        mgr = ShadowBranchManager()
        assert mgr.is_write_heavy_operation("bash", {"command": "echo x > /tmp/file"}) is True


class TestNoOpShadowManager:
    def test_all_methods_return_safe_defaults(self):
        mgr = NoOpShadowManager()
        assert mgr.create_shadow("x") is None
        assert mgr.restore_shadow("x") is False
        assert mgr.list_shadows() == []
        assert mgr.clean_shadows() == 0
        assert mgr.is_write_heavy_operation("bash", {}) is False
