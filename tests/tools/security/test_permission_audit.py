"""Tests for the permission auditing module."""



from vibe.tools.security.audit import AuditSeverity, SecurityAuditLogger
from vibe.tools.security.permission_audit import PermissionAuditor


class TestPermissionAuditor:
    """Test PermissionAuditor checks."""

    def setup_method(self):
        SecurityAuditLogger.reset_instance()

    def teardown_method(self):
        SecurityAuditLogger.reset_instance()

    def test_check_state_directory_secure(self, tmp_path):
        """Secure state directory passes."""
        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_state_directory()
        # Directory exists with default permissions, should not be world-writable
        assert not any(r.is_violation for r in results if r)

    def test_check_state_directory_world_writable(self, tmp_path):
        """World-writable state directory is flagged as critical."""
        tmp_path.chmod(0o777)
        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_state_directory()
        violation = [r for r in results if r and r.is_violation]
        assert len(violation) == 1
        assert violation[0].severity == AuditSeverity.CRITICAL
        assert "WORLD-WRITABLE" in violation[0].message

    def test_check_config_file_permissions(self, tmp_path):
        """Config file with wrong permissions is flagged."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o644)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_config_file()
        # World-readable sensitive file is now CRITICAL
        assert any(r for r in results if r and r.severity == AuditSeverity.CRITICAL)

    def test_check_config_file_secure(self, tmp_path):
        """Config file with 0o600 passes."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o600)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_config_file()
        assert not any(r for r in results if r and r.is_violation)

    def test_detect_synced_folder_dropbox(self, tmp_path):
        """Detects Dropbox synced folder."""
        dropbox_dir = tmp_path / "Dropbox" / ".vibe"
        dropbox_dir.mkdir(parents=True)
        (tmp_path / "Dropbox" / ".dropbox").mkdir()

        auditor = PermissionAuditor(state_dir=str(dropbox_dir))
        warning = auditor.detect_synced_folder()
        assert warning is not None
        assert "synced" in warning.lower() or "cloud" in warning.lower()

    def test_detect_synced_folder_icloud(self, tmp_path):
        """Detects iCloud synced folder."""
        icloud_dir = tmp_path / "iCloud Drive" / ".vibe"
        icloud_dir.mkdir(parents=True)

        auditor = PermissionAuditor(state_dir=str(icloud_dir))
        warning = auditor.detect_synced_folder()
        assert warning is not None
        assert "icloud" in warning.lower()

    def test_no_synced_folder_warning(self, tmp_path):
        """No warning for normal directory."""
        auditor = PermissionAuditor(state_dir=str(tmp_path))
        warning = auditor.detect_synced_folder()
        assert warning is None

    def test_run_all_checks(self, tmp_path):
        """run_all_checks returns all violations."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o644)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        violations = auditor.run_all_checks()
        assert len(violations) >= 1

    def test_print_warnings(self, tmp_path, capsys):
        """Warnings are printed to stderr."""
        tmp_path.chmod(0o777)
        auditor = PermissionAuditor(state_dir=str(tmp_path))
        auditor.run_all_checks()
        auditor.print_warnings()
        captured = capsys.readouterr()
        assert "WORLD-WRITABLE" in captured.err

    def test_has_critical_violations(self, tmp_path):
        """has_critical_violations returns True for world-writable."""
        tmp_path.chmod(0o777)
        auditor = PermissionAuditor(state_dir=str(tmp_path))
        auditor.run_all_checks()
        assert auditor.has_critical_violations()

    def test_no_critical_violations_when_secure(self, tmp_path):
        """has_critical_violations returns False for secure directory."""
        auditor = PermissionAuditor(state_dir=str(tmp_path))
        auditor.run_all_checks()
        assert not auditor.has_critical_violations()

    def test_check_approval_store(self, tmp_path):
        """Approval store permissions are checked."""
        store_file = tmp_path / "approvals.json"
        store_file.write_text("{}")
        store_file.chmod(0o644)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_approval_store()
        # World-readable sensitive file is now CRITICAL
        assert any(r for r in results if r and r.severity == AuditSeverity.CRITICAL)

    def test_check_log_directory(self, tmp_path):
        """Log directory permissions are checked."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_dir.chmod(0o755)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_log_directory()
        assert any(r for r in results if r and r.severity == AuditSeverity.WARNING)

    def test_synced_folder_marker_file(self, tmp_path):
        """Detects synced folder by marker file."""
        synced_dir = tmp_path / "synced"
        synced_dir.mkdir()
        (synced_dir / ".dropbox.attr").write_text("")
        vibe_dir = synced_dir / ".vibe"
        vibe_dir.mkdir()

        auditor = PermissionAuditor(state_dir=str(vibe_dir))
        warning = auditor.detect_synced_folder()
        assert warning is not None

    def test_symlink_detected(self, tmp_path):
        """Symlinks in state directory are flagged as critical."""
        real_file = tmp_path / "real_config.yaml"
        real_file.write_text("test: true")
        symlink = tmp_path / "config.yaml"
        symlink.symlink_to(real_file)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_config_file()
        assert any(r for r in results if r and "SYMLINK" in r.message)
        assert any(r for r in results if r and r.severity == AuditSeverity.CRITICAL)

    def test_group_writable_sensitive_file(self, tmp_path):
        """Group-writable sensitive files are flagged."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o660)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_config_file()
        assert any(r for r in results if r and "GROUP-WRITABLE" in r.message)

    def test_group_readable_sensitive_file(self, tmp_path):
        """Group-readable sensitive files are flagged."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o640)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_config_file()
        assert any(r for r in results if r and "group-readable" in r.message)

    def test_world_readable_approval_store_critical(self, tmp_path):
        """World-readable approval store is CRITICAL."""
        store_file = tmp_path / "approvals.json"
        store_file.write_text("{}")
        store_file.chmod(0o644)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_approval_store()
        assert any(r for r in results if r and r.severity == AuditSeverity.CRITICAL)
        assert any(r for r in results if r and "world-readable" in r.message)

    def test_overlapping_permissions_critical_wins(self, tmp_path):
        """0o664 (group-writable + world-readable) should be CRITICAL, not WARNING."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o664)

        auditor = PermissionAuditor(state_dir=str(tmp_path))
        results = auditor.check_config_file()
        # Should be CRITICAL (world-readable) not WARNING (group-writable)
        assert any(r for r in results if r and r.severity == AuditSeverity.CRITICAL)
        assert any(r for r in results if r and "world-readable" in r.message)

