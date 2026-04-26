"""Tests for security configuration in vibe/core/config.py."""

import os
import tempfile
from pathlib import Path

import pytest

from vibe.core.config import (
    VibeConfig,
    SecurityConfig,
    FileSafetyConfig,
    EnvSanitizationConfig,
    SandboxConfig,
    AuditConfig,
)


class TestSecurityConfigDefaults:
    """Test default security configuration values."""

    def test_default_approval_mode(self):
        cfg = SecurityConfig()
        assert cfg.approval_mode == "smart"

    def test_default_flags(self):
        cfg = SecurityConfig()
        assert cfg.dangerous_patterns_enabled is True
        assert cfg.secret_redaction is True
        assert cfg.audit_logging is True
        assert cfg.fail_closed is True

    def test_default_nested_configs(self):
        cfg = SecurityConfig()
        assert isinstance(cfg.file_safety, FileSafetyConfig)
        assert isinstance(cfg.env_sanitization, EnvSanitizationConfig)
        assert isinstance(cfg.sandbox, SandboxConfig)
        assert isinstance(cfg.audit, AuditConfig)

    def test_file_safety_defaults(self):
        fs = FileSafetyConfig()
        assert fs.write_denylist_enabled is True
        assert fs.read_blocklist_enabled is True
        assert fs.safe_root is None

    def test_env_sanitization_defaults(self):
        env = EnvSanitizationConfig()
        assert env.enabled is True
        assert env.block_path_overrides is True
        assert env.strip_shell_env is True
        assert env.secret_prefixes == [
            "*_API_KEY", "*_TOKEN", "*_SECRET", "AWS_*", "GITHUB_*"
        ]

    def test_sandbox_defaults(self):
        sb = SandboxConfig()
        assert sb.backend == "local"
        assert sb.auto_approve_in_sandbox is False

    def test_audit_defaults(self):
        audit = AuditConfig()
        assert audit.log_path == os.path.expanduser("~/.vibe/logs/security.log")
        assert audit.max_events == 10000
        assert audit.redact_in_logs is True


class TestSecurityConfigValidation:
    """Test security configuration validation."""

    def test_invalid_approval_mode_raises(self):
        with pytest.raises(ValueError, match="approval_mode must be one of"):
            SecurityConfig(approval_mode="invalid")

    def test_invalid_sandbox_backend_raises(self):
        with pytest.raises(ValueError, match="sandbox.backend must be one of"):
            SandboxConfig(backend="invalid")

    def test_invalid_audit_max_events_raises(self):
        with pytest.raises(ValueError, match="max_events must be >= 1"):
            AuditConfig(max_events=0)

    def test_safe_root_nonexistent_raises(self):
        with pytest.raises(ValueError, match="safe_root does not exist"):
            FileSafetyConfig(safe_root="/nonexistent/path/12345")

    def test_safe_root_existing_path_works(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs = FileSafetyConfig(safe_root=tmpdir)
            assert fs.safe_root == str(Path(tmpdir).resolve())


class TestSecurityConfigMethods:
    """Test SecurityConfig helper methods."""

    def test_is_approval_required_manual(self):
        cfg = SecurityConfig(approval_mode="manual")
        assert cfg.is_approval_required() is True

    def test_is_approval_required_smart(self):
        cfg = SecurityConfig(approval_mode="smart")
        assert cfg.is_approval_required() is True

    def test_is_approval_required_auto(self):
        cfg = SecurityConfig(approval_mode="auto")
        assert cfg.is_approval_required() is False

    def test_is_auto_approve(self):
        assert SecurityConfig(approval_mode="auto").is_auto_approve() is True
        assert SecurityConfig(approval_mode="smart").is_auto_approve() is False
        assert SecurityConfig(approval_mode="manual").is_auto_approve() is False


class TestVibeConfigLoading:
    """Test VibeConfig loads security section correctly."""

    def test_load_with_security_section(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text('''
llm:
  default_model: "test-model"
  base_url: "http://localhost:11434"
security:
  approval_mode: "manual"
  dangerous_patterns_enabled: false
  secret_redaction: false
  audit_logging: false
  fail_closed: false
  file_safety:
    write_denylist_enabled: false
    read_blocklist_enabled: false
    safe_root: "{tmpdir}"
  env_sanitization:
    enabled: false
    block_path_overrides: false
    strip_shell_env: false
    secret_prefixes: ["CUSTOM_*"]
  sandbox:
    backend: "docker"
    auto_approve_in_sandbox: true
  audit:
    log_path: "{tmpdir}/security.log"
    max_events: 5000
    redact_in_logs: false
'''.format(tmpdir=tmp_path))

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        sec = cfg.get_security_config()

        assert sec.approval_mode == "manual"
        assert sec.dangerous_patterns_enabled is False
        assert sec.secret_redaction is False
        assert sec.audit_logging is False
        assert sec.fail_closed is False
        assert sec.file_safety.write_denylist_enabled is False
        assert sec.file_safety.read_blocklist_enabled is False
        assert sec.file_safety.safe_root == str(tmp_path.resolve())
        assert sec.env_sanitization.enabled is False
        assert sec.env_sanitization.secret_prefixes == ["CUSTOM_*"]
        assert sec.sandbox.backend == "docker"
        assert sec.sandbox.auto_approve_in_sandbox is True
        assert sec.audit.max_events == 5000
        assert sec.audit.redact_in_logs is False

    def test_load_without_security_section_uses_defaults(self, tmp_path):
        """Backward compatibility: config without security section loads defaults."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text('''
llm:
  default_model: "test-model"
  base_url: "http://localhost:11434"
''')

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        sec = cfg.get_security_config()

        assert sec.approval_mode == "smart"
        assert sec.dangerous_patterns_enabled is True
        assert sec.secret_redaction is True
        assert sec.audit_logging is True
        assert sec.fail_closed is True
        assert sec.file_safety.write_denylist_enabled is True
        assert sec.env_sanitization.enabled is True
        assert sec.sandbox.backend == "local"
        assert sec.audit.max_events == 10000

    def test_load_empty_config_uses_defaults(self, tmp_path):
        """Empty config uses all defaults including security."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("# Just a comment\n")

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        sec = cfg.get_security_config()

        assert sec.approval_mode == "smart"
        assert sec.fail_closed is True

    def test_load_partial_security_section(self, tmp_path):
        """Partial security section merges with defaults."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text('''
llm:
  default_model: "test-model"
security:
  approval_mode: "auto"
  file_safety:
    write_denylist_enabled: false
''')

        cfg = VibeConfig.load(path=config_path, auto_create=False)
        sec = cfg.get_security_config()

        assert sec.approval_mode == "auto"
        # Unspecified fields use defaults
        assert sec.dangerous_patterns_enabled is True
        assert sec.secret_redaction is True
        assert sec.file_safety.write_denylist_enabled is False
        assert sec.file_safety.read_blocklist_enabled is True  # default
        assert sec.sandbox.backend == "local"  # default


class TestVibeConfigEnvOverrides:
    """Test that env vars can override security config (future-proofing)."""

    def test_env_approval_mode_override(self, tmp_path, monkeypatch):
        """VIBE_APPROVAL_MODE env var overrides config file."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text('''
llm:
  default_model: "test-model"
security:
  approval_mode: "manual"
''')

        monkeypatch.setenv("VIBE_APPROVAL_MODE", "auto")
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        sec = cfg.get_security_config()
        assert sec.approval_mode == "auto"

    def test_env_approval_mode_unset_uses_file(self, tmp_path, monkeypatch):
        """Without env var, config file value is used."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text('''
llm:
  default_model: "test-model"
security:
  approval_mode: "manual"
''')

        monkeypatch.delenv("VIBE_APPROVAL_MODE", raising=False)
        cfg = VibeConfig.load(path=config_path, auto_create=False)
        sec = cfg.get_security_config()
        assert sec.approval_mode == "manual"
