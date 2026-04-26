"""Security audit logging framework for vibe-agent.

Provides structured security event logging with rotation, redaction,
and fail-safe behavior.
"""

import json
import logging
import logging.handlers
import os
import re
import stat
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class AuditEventType(str, Enum):
    """Standardized security audit event types."""

    COMMAND_BLOCKED = "command_blocked"
    COMMAND_APPROVED = "command_approved"
    COMMAND_FLAGGED = "command_flagged"
    FILE_WRITE_DENIED = "file_write_denied"
    FILE_READ_DENIED = "file_read_denied"
    PATH_TRAVERSAL_ATTEMPT = "path_traversal_attempt"
    SECRET_REDACTED = "secret_redacted"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REVOKED = "approval_revoked"
    ENV_SANITIZED = "env_sanitized"
    URL_BLOCKED = "url_blocked"
    HOOK_BLOCKED = "hook_blocked"
    CHECKPOINT_CREATED = "checkpoint_created"
    CHECKPOINT_RESTORED = "checkpoint_restored"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


@dataclass
class AuditEvent:
    """A single security audit event."""

    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str = ""
    tool_name: str = ""
    command: str = ""
    pattern: str = ""
    user_decision: str = ""
    llm_decision: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "command": self.command,
            "pattern": self.pattern,
            "user_decision": self.user_decision,
            "llm_decision": self.llm_decision,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class _SecureRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that enforces 0o600 permissions on every new file."""

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return stream


class SecurityAuditLogger:
    """Singleton security audit logger with rotation and redaction.

    Thread-safe. Falls back to stderr if file logging fails.
    """

    _instance: Optional["SecurityAuditLogger"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        log_path: str = "~/.vibe/logs/security.log",
        max_bytes: int = 10 * 1024 * 1024,  # 10 MB
        backup_count: int = 5,
        redact_in_logs: bool = True,
    ):
        with self._lock:
            if getattr(self, "_initialized", False):
                return

            self._initialized = True
            self.log_path = os.path.expanduser(log_path)
            self.max_bytes = max_bytes
            self.backup_count = backup_count
            self.redact_in_logs = redact_in_logs
            self._logger: Optional[logging.Logger] = None
            self._handler: Optional[_SecureRotatingFileHandler] = None
            self._fallback_to_stderr = False

            try:
                self._setup_logger()
            except Exception as exc:
                # Fail-safe: log to stderr and continue (application keeps running)
                self._fallback_to_stderr = True
                print(
                    f"[SECURITY AUDIT WARNING] Failed to initialize file logger: {exc}. "
                    f"Falling back to stderr.",
                    file=sys.stderr,
                )

    def _setup_logger(self) -> None:
        """Set up the rotating file logger."""
        log_dir = os.path.dirname(self.log_path)
        if log_dir:
            os.makedirs(log_dir, mode=0o700, exist_ok=True)

        self._logger = logging.getLogger("vibe.security.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        # Clear any existing handlers to avoid duplicates on re-init
        self._logger.handlers.clear()

        self._handler = _SecureRotatingFileHandler(
            self.log_path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding="utf-8",
        )
        self._handler.setLevel(logging.INFO)

        # Simple formatter: JSON lines
        formatter = logging.Formatter("%(message)s")
        self._handler.setFormatter(formatter)
        self._logger.addHandler(self._handler)

    def _redact(self, text: str) -> str:
        """Basic redaction of common secret patterns.

        This is a lightweight pre-filter. Full redaction is done by
        the dedicated redaction module (Phase 5).
        """
        if not self.redact_in_logs or not text:
            return text

        redacted = text
        # API keys: sk-..., ghp_..., etc.
        redacted = re.sub(r"sk-[a-zA-Z0-9]{24,}", "[REDACTED:API_KEY]", redacted)
        redacted = re.sub(r"ghp_[a-zA-Z0-9]{36}", "[REDACTED:GITHUB_TOKEN]", redacted)
        redacted = re.sub(r"gho_[a-zA-Z0-9]{36}", "[REDACTED:GITHUB_OAUTH]", redacted)
        # Generic token patterns (includes JWT chars: . - _ + / =)
        redacted = re.sub(
            r"[a-zA-Z0-9_-]*token[a-zA-Z0-9_-]*[=:]\s*['\"]?[a-zA-Z0-9.\-_+/=]{16,}['\"]?",
            "[REDACTED:TOKEN]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"[a-zA-Z0-9_-]*api[_-]?key[a-zA-Z0-9_-]*[=:]\s*['\"]?[a-zA-Z0-9.\-_+/=]{8,}['\"]?",
            "[REDACTED:API_KEY]",
            redacted,
            flags=re.IGNORECASE,
        )
        # Bearer tokens / JWTs
        redacted = re.sub(
            r"Bearer\s+[a-zA-Z0-9.\-_+/=]{20,}",
            "[REDACTED:BEARER_TOKEN]",
            redacted,
        )
        return redacted

    def _redact_value(self, value: Any) -> Any:
        """Recursively redact secrets in nested dicts/lists."""
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, dict):
            return {str(k): self._redact_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            # Normalize to list to avoid namedtuple/custom tuple instantiation crashes
            return [self._redact_value(item) for item in value]
        if isinstance(value, (int, float, bool, type(None))):
            return value
        # Fallback: stringify and redact any custom objects (exceptions, datetimes, etc.)
        return self._redact(str(value))

    def _redact_event(self, event: AuditEvent) -> AuditEvent:
        """Return a copy of the event with sensitive fields redacted."""
        if not self.redact_in_logs:
            return event

        return AuditEvent(
            event_type=event.event_type,
            severity=event.severity,
            timestamp=event.timestamp,
            session_id=event.session_id,
            tool_name=event.tool_name,
            command=self._redact(event.command),
            pattern=event.pattern,
            user_decision=event.user_decision,
            llm_decision=event.llm_decision,
            metadata=self._redact_value(event.metadata),
        )

    def log(self, event: AuditEvent) -> None:
        """Log a security audit event.

        Thread-safe. Falls back to stderr if file logger is unavailable.
        Also catches any exception during redaction/serialization to ensure
        audit logging never crashes the application.
        """
        try:
            redacted_event = self._redact_event(event)
            json_line = redacted_event.to_json()
        except Exception as exc:
            # Fail-safe: if redaction/serialization fails, log raw event to stderr
            print(
                f"[AUDIT SERIALIZATION FALLBACK] Failed to prepare audit event: {exc}. "
                f"Raw event type: {event.event_type.value}",
                file=sys.stderr,
            )
            return

        if self._fallback_to_stderr or self._logger is None:
            print(f"[AUDIT] {json_line}", file=sys.stderr)
            return

        try:
            self._logger.info(json_line)
        except Exception:
            # Fail-safe: never let audit logging failure block execution
            print(f"[AUDIT FALLBACK] {json_line}", file=sys.stderr)

    def log_command_blocked(
        self,
        command: str,
        pattern: str,
        session_id: str = "",
        tool_name: str = "bash",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convenience method for logging blocked commands."""
        self.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_BLOCKED,
                severity=AuditSeverity.CRITICAL,
                session_id=session_id,
                tool_name=tool_name,
                command=command,
                pattern=pattern,
                metadata=metadata or {},
            )
        )

    def log_command_approved(
        self,
        command: str,
        session_id: str = "",
        tool_name: str = "bash",
        user_decision: str = "",
        llm_decision: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convenience method for logging approved commands."""
        self.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_APPROVED,
                severity=AuditSeverity.INFO,
                session_id=session_id,
                tool_name=tool_name,
                command=command,
                user_decision=user_decision,
                llm_decision=llm_decision,
                metadata=metadata or {},
            )
        )

    def log_file_write_denied(
        self,
        path: str,
        reason: str,
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convenience method for logging denied file writes."""
        self.log(
            AuditEvent(
                event_type=AuditEventType.FILE_WRITE_DENIED,
                severity=AuditSeverity.WARNING,
                session_id=session_id,
                tool_name="write_file",
                command=path,
                pattern=reason,
                metadata=metadata or {},
            )
        )

    def log_path_traversal_attempt(
        self,
        path: str,
        root_dir: str,
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convenience method for logging path traversal attempts."""
        self.log(
            AuditEvent(
                event_type=AuditEventType.PATH_TRAVERSAL_ATTEMPT,
                severity=AuditSeverity.CRITICAL,
                session_id=session_id,
                tool_name="file",
                command=path,
                pattern=f"escapes {root_dir}",
                metadata=metadata or {},
            )
        )

    def close(self) -> None:
        """Clean up logger handlers."""
        if self._handler:
            self._handler.close()
            self._logger.handlers.clear()

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        with cls._lock:
            if cls._instance:
                cls._instance.close()
                cls._instance = None
