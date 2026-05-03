"""Tests for environment sanitization."""

import os

from vibe.tools.security.env_sanitizer import (
    MAX_ENV_VALUE_SIZE,
    EnvSanitizer,
    sanitize_env,
)


class TestEnvSanitizer:
    """Test EnvSanitizer."""

    def test_allows_safe_vars(self):
        sanitizer = EnvSanitizer()
        env = {"HOME": "/home/user", "PATH": "/usr/bin", "LANG": "en_US.UTF-8"}
        result = sanitizer.sanitize(env)
        assert result["HOME"] == "/home/user"
        assert result["PATH"] == "/usr/bin"

    def test_strips_secret_prefixes(self):
        sanitizer = EnvSanitizer()
        env = {"MY_SECRET_KEY": "abc123", "API_KEY_PROD": "xyz789", "SAFE_VAR": "ok"}
        result = sanitizer.sanitize(env)
        assert "MY_SECRET_KEY" not in result
        assert "API_KEY_PROD" not in result
        assert "SAFE_VAR" in result

    def test_strips_base64_credentials(self):
        sanitizer = EnvSanitizer()
        env = {"SOME_VAR": "dGVzdHN0cmluZzEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg=="}
        result = sanitizer.sanitize(env)
        assert "SOME_VAR" not in result

    def test_limits_value_size(self):
        sanitizer = EnvSanitizer()
        env = {"LARGE_VAR": "x" * (MAX_ENV_VALUE_SIZE + 1)}
        result = sanitizer.sanitize(env)
        assert "LARGE_VAR" not in result

    def test_block_path_override(self):
        sanitizer = EnvSanitizer()
        env = {"PATH": "/malicious/bin"}
        result = sanitizer.block_path_override(env)
        assert result["PATH"] == os.environ.get("PATH", "")

    def test_strip_for_shell(self):
        sanitizer = EnvSanitizer()
        env = {"HOME": "/home/user", "SECRET": "abc", "TERM": "xterm"}
        result = sanitizer.strip_for_shell(env)
        assert "HOME" in result
        assert "TERM" in result
        assert "SECRET" not in result

    def test_convenience_function(self):
        env = {"API_KEY": "secret123", "HOME": "/tmp"}
        result = sanitize_env(env)
        assert "API_KEY" not in result
        assert "HOME" in result
