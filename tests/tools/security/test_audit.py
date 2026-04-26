"""Tests for the security audit logging framework."""

import json
import threading
from pathlib import Path

import pytest

from vibe.tools.security.audit import (
    AuditEvent,
    AuditEventType,
    AuditSeverity,
    SecurityAuditLogger,
)


class TestAuditEvent:
    """Test AuditEvent dataclass."""

    def test_event_creation(self):
        event = AuditEvent(
            event_type=AuditEventType.COMMAND_BLOCKED,
            severity=AuditSeverity.CRITICAL,
            session_id="sess-123",
            tool_name="bash",
            command="rm -rf /",
            pattern=r"rm\s+-rf\s+/+",
        )
        assert event.event_type == AuditEventType.COMMAND_BLOCKED
        assert event.severity == AuditSeverity.CRITICAL
        assert event.session_id == "sess-123"
        assert event.command == "rm -rf /"

    def test_event_to_dict(self):
        event = AuditEvent(
            event_type=AuditEventType.COMMAND_BLOCKED,
            severity=AuditSeverity.CRITICAL,
            command="rm -rf /",
        )
        d = event.to_dict()
        assert d["event_type"] == "command_blocked"
        assert d["severity"] == "critical"
        assert d["command"] == "rm -rf /"
        assert "timestamp" in d

    def test_event_to_json(self):
        event = AuditEvent(
            event_type=AuditEventType.COMMAND_BLOCKED,
            severity=AuditSeverity.CRITICAL,
            command="rm -rf /",
        )
        j = event.to_json()
        parsed = json.loads(j)
        assert parsed["event_type"] == "command_blocked"
        assert parsed["command"] == "rm -rf /"


class TestSecurityAuditLogger:
    """Test SecurityAuditLogger singleton behavior and logging."""

    def setup_method(self):
        """Reset singleton before each test."""
        SecurityAuditLogger.reset_instance()

    def teardown_method(self):
        """Reset singleton after each test."""
        SecurityAuditLogger.reset_instance()

    def test_singleton(self):
        logger1 = SecurityAuditLogger()
        logger2 = SecurityAuditLogger()
        assert logger1 is logger2

    def test_logger_creates_file(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        assert log_file.exists()

    def test_log_command_blocked(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        logger.log_command_blocked(
            command="rm -rf /",
            pattern=r"rm\s+-rf\s+/+",
            session_id="test-session",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert parsed["event_type"] == "command_blocked"
        assert parsed["severity"] == "critical"
        assert parsed["command"] == "rm -rf /"
        assert parsed["pattern"] == r"rm\s+-rf\s+/+"
        assert parsed["session_id"] == "test-session"

    def test_log_command_approved(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        logger.log_command_approved(
            command="ls -la",
            user_decision="once",
            llm_decision="APPROVE",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert parsed["event_type"] == "command_approved"
        assert parsed["user_decision"] == "once"
        assert parsed["llm_decision"] == "APPROVE"

    def test_log_file_write_denied(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        logger.log_file_write_denied(
            path="/etc/passwd",
            reason="write_denylist",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert parsed["event_type"] == "file_write_denied"
        assert parsed["command"] == "/etc/passwd"
        assert parsed["pattern"] == "write_denylist"

    def test_log_path_traversal_attempt(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        logger.log_path_traversal_attempt(
            path="/etc/shadow",
            root_dir="/tmp/workspace",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert parsed["event_type"] == "path_traversal_attempt"
        assert parsed["command"] == "/etc/shadow"
        assert "escapes /tmp/workspace" in parsed["pattern"]

    def test_redaction_in_logs(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)
        logger.log_command_blocked(
            command="curl -H 'Authorization: Bearer sk-test12345678901234567890' http://example.com",
            pattern="curl",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert "sk-test12345678901234567890" not in parsed["command"]
        assert "[REDACTED" in parsed["command"]

    def test_no_redaction_when_disabled(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=False)
        logger.log_command_blocked(
            command="sk-test12345678901234567890",
            pattern="test",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert "sk-test12345678901234567890" in parsed["command"]

    def test_fallback_to_stderr_on_bad_path(self, capsys):
        """Logger falls back to stderr when log path is invalid."""
        logger = SecurityAuditLogger(
            log_path="/nonexistent/dir/that/cannot/be/created/security.log"
        )
        assert logger._fallback_to_stderr is True

        logger.log_command_blocked(command="test", pattern="test")
        captured = capsys.readouterr()
        assert "[AUDIT FALLBACK]" in captured.err or "[AUDIT]" in captured.err

    def test_concurrent_logging(self, tmp_path):
        """Multiple threads can log simultaneously."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))

        def worker(n):
            logger.log_command_blocked(
                command=f"cmd-{n}",
                pattern="pattern",
                session_id=f"sess-{n}",
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logger.close()
        SecurityAuditLogger.reset_instance()

        lines = [line for line in log_file.read_text().strip().split("\n") if line]
        assert len(lines) == 10
        for line in lines:
            parsed = json.loads(line)
            assert parsed["event_type"] == "command_blocked"

    def test_concurrent_initialization(self, tmp_path):
        """Multiple threads initializing logger simultaneously — race condition test."""
        log_file = tmp_path / "security.log"
        loggers = []

        def worker():
            logger = SecurityAuditLogger(log_path=str(log_file))
            loggers.append(logger)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same instance
        assert len(set(id(l) for l in loggers)) == 1

        # Should be able to log without errors
        loggers[0].log_command_blocked(command="test", pattern="test")
        loggers[0].close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        assert "command_blocked" in content

    def test_log_rotation(self, tmp_path):
        """Test that rotation happens when max_bytes is exceeded."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(
            log_path=str(log_file),
            max_bytes=100,
            backup_count=2,
        )

        for i in range(20):
            logger.log_command_blocked(
                command=f"command-with-long-text-to-exceed-size-{i}",
                pattern="pattern",
            )

        logger.close()
        SecurityAuditLogger.reset_instance()

        backup_files = list(tmp_path.glob("security.log*"))
        assert len(backup_files) >= 2

    def test_metadata_roundtrip(self, tmp_path):
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        logger.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_FLAGGED,
                severity=AuditSeverity.WARNING,
                metadata={"iteration": 5, "model": "test-model"},
            )
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert parsed["metadata"]["iteration"] == 5
        assert parsed["metadata"]["model"] == "test-model"

    def test_nested_metadata_redaction(self, tmp_path):
        """Secrets in nested metadata dicts/lists are redacted."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)
        logger.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_FLAGGED,
                severity=AuditSeverity.WARNING,
                metadata={
                    "headers": {"Authorization": "Bearer sk-nested12345678901234567890"},
                    "tokens": ["ghp_123456789012345678901234567890123456", "safe-value"],
                    "flat": "sk-flat12345678901234567890",
                },
            )
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        # Nested dict
        assert "sk-nested12345678901234567890" not in parsed["metadata"]["headers"]["Authorization"]
        assert "[REDACTED" in parsed["metadata"]["headers"]["Authorization"]
        # Nested list
        assert "ghp_123456789012345678901234567890123456" not in parsed["metadata"]["tokens"][0]
        assert "[REDACTED" in parsed["metadata"]["tokens"][0]
        # Safe value preserved
        assert parsed["metadata"]["tokens"][1] == "safe-value"
        # Flat string
        assert "sk-flat12345678901234567890" not in parsed["metadata"]["flat"]

    def test_jwt_redaction(self, tmp_path):
        """JWT tokens are redacted."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        logger.log_command_blocked(
            command=f"curl -H 'Authorization: Bearer {jwt}'",
            pattern="curl",
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        # JWT contains dots, should be caught by expanded regex
        assert jwt not in content

    def test_file_permissions(self, tmp_path):
        """Log files are created with restrictive 0o600 permissions."""
        import os
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file))
        logger.log_command_blocked(command="test", pattern="test")
        logger.close()
        SecurityAuditLogger.reset_instance()

        mode = os.stat(log_file).st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"

    def test_custom_object_redaction(self, tmp_path):
        """Custom objects in metadata are stringified and redacted."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)

        class CustomObj:
            def __str__(self):
                return "sk-custom12345678901234567890"

        logger.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_FLAGGED,
                severity=AuditSeverity.WARNING,
                metadata={"obj": CustomObj(), "num": 42},
            )
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        assert "sk-custom12345678901234567890" not in parsed["metadata"]["obj"]
        assert "[REDACTED" in parsed["metadata"]["obj"]
        assert parsed["metadata"]["num"] == 42

    def test_namedtuple_in_metadata(self, tmp_path):
        """NamedTuples in metadata don't crash during redaction."""
        from collections import namedtuple
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)

        Token = namedtuple("Token", ["value"])
        logger.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_FLAGGED,
                severity=AuditSeverity.WARNING,
                metadata={"tokens": [Token("sk-nt123456789012345678901234567")]},
            )
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        # NamedTuple should be converted to list, then string redacted
        assert "sk-nt123456789012345678901234567" not in str(parsed["metadata"])
        assert "[REDACTED" in str(parsed["metadata"])

    def test_non_string_dict_keys(self, tmp_path):
        """Non-string dict keys are stringified for JSON serialization."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)
        logger.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_FLAGGED,
                severity=AuditSeverity.WARNING,
                metadata={(1, 2): "value", "normal_key": "safe"},
            )
        )
        logger.close()
        SecurityAuditLogger.reset_instance()

        content = log_file.read_text()
        parsed = json.loads(content.strip())
        # Tuple key should be stringified
        assert "(1, 2)" in parsed["metadata"] or "1, 2" in str(parsed["metadata"])
        assert parsed["metadata"]["normal_key"] == "safe"

    def test_serialization_failure_fallback(self, tmp_path, capsys):
        """If redaction/serialization fails, log() catches it and doesn't crash."""
        log_file = tmp_path / "security.log"
        logger = SecurityAuditLogger(log_path=str(log_file), redact_in_logs=True)

        class BadObj:
            def __str__(self):
                raise RuntimeError("bad object")

        # This should NOT raise an exception
        logger.log(
            AuditEvent(
                event_type=AuditEventType.COMMAND_FLAGGED,
                severity=AuditSeverity.WARNING,
                metadata={"bad": BadObj()},
            )
        )
        captured = capsys.readouterr()
        assert "[AUDIT SERIALIZATION FALLBACK]" in captured.err
        # No exception raised = test passes
