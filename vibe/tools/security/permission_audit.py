"""Permission auditing for vibe-agent state directory.

Checks ~/.vibe/ permissions on startup, warns about insecure configs,
and detects synced/cloud folders that may leak secrets.
"""

import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from vibe.tools.security.audit import (
    AuditEvent,
    AuditEventType,
    AuditSeverity,
    SecurityAuditLogger,
)


@dataclass
class PermissionCheckResult:
    """Result of a single permission check."""

    path: str
    expected_mode: int
    actual_mode: int
    is_violation: bool
    severity: AuditSeverity
    message: str


class PermissionAuditor:
    """Audits vibe-agent state directory permissions on startup."""

    # Cloud/synced folder indicators (from OpenClaw research)
    SYNCED_FOLDER_MARKERS = [
        ".icloud",
        ".dropbox",
        ".dropbox.attr",
        ".onedrive",
        ".gdrive",
        ".synology",
        ".box",
        ".pcloud",
        ".mega",
        ".nextcloud",
        ".owncloud",
    ]

    def __init__(
        self,
        state_dir: str = "~/.vibe",
        logger: Optional[SecurityAuditLogger] = None,
    ):
        self.state_dir = Path(os.path.expanduser(state_dir))
        self.logger = logger or SecurityAuditLogger()
        self.violations: List[PermissionCheckResult] = []
        self.warnings: List[str] = []

    def _check_path_permissions(
        self,
        path: Path,
        expected_mode: int,
        description: str,
        is_sensitive: bool = False,
    ) -> Optional[PermissionCheckResult]:
        """Check if a path has the expected permissions."""
        if not path.exists():
            return None

        try:
            actual_mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError:
            return None

        # Check if any "other" permissions are set (world-readable/writable)
        other_perms = actual_mode & 0o007
        group_perms = (actual_mode >> 3) & 0o007

        is_violation = False
        severity = AuditSeverity.INFO
        message = f"{description}: 0o{actual_mode:o}"

        # Detect symlinks (TOCTOU protection)
        if path.is_symlink():
            is_violation = True
            severity = AuditSeverity.CRITICAL
            message = f"{description} is a SYMLINK: {path.resolve()}"

        # World-writable is always a violation
        elif other_perms & 0o002:
            is_violation = True
            severity = AuditSeverity.CRITICAL
            message = f"{description} is WORLD-WRITABLE: 0o{actual_mode:o}"

        # World-readable for sensitive files is critical
        elif is_sensitive and (other_perms & 0o004):
            is_violation = True
            severity = AuditSeverity.CRITICAL
            message = f"{description} is world-readable: 0o{actual_mode:o}"

        # Group-writable on sensitive files is a violation
        elif is_sensitive and (group_perms & 0o002):
            is_violation = True
            severity = AuditSeverity.WARNING
            message = f"{description} is GROUP-WRITABLE: 0o{actual_mode:o}"

        # Group-readable for sensitive files is a warning
        elif is_sensitive and (group_perms & 0o004):
            is_violation = True
            severity = AuditSeverity.WARNING
            message = f"{description} is group-readable: 0o{actual_mode:o}"

        # Expected mode mismatch (not a violation but worth noting)
        elif actual_mode != expected_mode:
            severity = AuditSeverity.WARNING
            message = f"{description} permissions 0o{actual_mode:o} differ from expected 0o{expected_mode:o}"

        result = PermissionCheckResult(
            path=str(path),
            expected_mode=expected_mode,
            actual_mode=actual_mode,
            is_violation=is_violation,
            severity=severity,
            message=message,
        )

        if is_violation or severity == AuditSeverity.WARNING:
            self.violations.append(result)

        return result

    def check_state_directory(self) -> List[PermissionCheckResult]:
        """Check the main state directory permissions."""
        return [
            self._check_path_permissions(
                self.state_dir,
                expected_mode=0o700,
                description="State directory",
            ),
        ]

    def check_config_file(self) -> List[PermissionCheckResult]:
        """Check config file permissions."""
        config_path = self.state_dir / "config.yaml"
        return [
            self._check_path_permissions(
                config_path,
                expected_mode=0o600,
                description="Config file",
                is_sensitive=True,
            ),
        ]

    def check_approval_store(self) -> List[PermissionCheckResult]:
        """Check approval store file permissions."""
        store_path = self.state_dir / "approvals.json"
        return [
            self._check_path_permissions(
                store_path,
                expected_mode=0o600,
                description="Approval store",
                is_sensitive=True,
            ),
        ]

    def check_log_directory(self) -> List[PermissionCheckResult]:
        """Check log directory permissions."""
        log_dir = self.state_dir / "logs"
        return [
            self._check_path_permissions(
                log_dir,
                expected_mode=0o700,
                description="Log directory",
            ),
        ]

    def detect_synced_folder(self) -> Optional[str]:
        """Detect if state directory is under a cloud/synced folder.

        Returns warning message if detected, None otherwise.
        """
        current = self.state_dir.resolve()

        # Walk up the directory tree looking for synced folder markers
        for parent in [current, *current.parents]:
            for marker in self.SYNCED_FOLDER_MARKERS:
                marker_path = parent / marker
                if marker_path.exists():
                    warning = (
                        f"State directory {self.state_dir} appears to be under a "
                        f"synced/cloud folder ({marker} detected at {parent}). "
                        f"Secrets and audit logs may be synced to the cloud."
                    )
                    self.warnings.append(warning)
                    return warning

            # Check for common cloud folder names in the path
            parent_name = parent.name.lower()
            if parent_name in [
                "icloud drive",
                "dropbox",
                "onedrive",
                "google drive",
                "my drive",
                "box",
                "pcloud",
                "mega",
                "nextcloud",
                "owncloud",
                "synologydrive",
            ]:
                warning = (
                    f"State directory {self.state_dir} appears to be under "
                    f"{parent_name} (detected in path). Secrets and audit logs may be synced."
                )
                self.warnings.append(warning)
                return warning

        return None

    def run_all_checks(self) -> List[PermissionCheckResult]:
        """Run all permission checks and return violations."""
        self.violations = []
        self.warnings = []

        self.check_state_directory()
        self.check_config_file()
        self.check_approval_store()
        self.check_log_directory()
        self.detect_synced_folder()

        # Log violations to audit log
        for violation in self.violations:
            self.logger.log(
                AuditEvent(
                    event_type=AuditEventType.COMMAND_FLAGGED,
                    severity=violation.severity,
                    command=violation.path,
                    pattern=f"expected 0o{violation.expected_mode:o}, got 0o{violation.actual_mode:o}",
                    metadata={"message": violation.message},
                )
            )

        # Log synced folder warning
        if self.warnings:
            for warning in self.warnings:
                self.logger.log(
                    AuditEvent(
                        event_type=AuditEventType.COMMAND_FLAGGED,
                        severity=AuditSeverity.WARNING,
                        command=str(self.state_dir),
                        pattern="synced_folder_detected",
                        metadata={"warning": warning},
                    )
                )

        return self.violations

    def print_warnings(self) -> None:
        """Print warnings to stderr for visibility."""
        for violation in self.violations:
            prefix = "[CRITICAL]" if violation.severity == AuditSeverity.CRITICAL else "[WARNING]"
            print(f"{prefix} {violation.message}", file=sys.stderr)

        for warning in self.warnings:
            print(f"[WARNING] {warning}", file=sys.stderr)

    def has_critical_violations(self) -> bool:
        """Return True if any critical violations were found."""
        return any(v.severity == AuditSeverity.CRITICAL for v in self.violations)
