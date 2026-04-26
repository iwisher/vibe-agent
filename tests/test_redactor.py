"""Tests for SecretRedactor."""

import pytest
from vibe.harness.security.redactor import (
    SecretRedactor,
    RedactionPattern,
    redact,
    get_default_redactor,
)


class TestSecretRedactorBasic:
    """Basic redaction tests."""

    def test_redacts_openai_key(self):
        r = SecretRedactor()
        # Real OpenAI keys are sk- followed by 48 alphanumeric chars
        key = "sk-" + "a" * 48
        text = f"My key is {key}"
        result = r.redact(text)
        assert "sk-" not in result
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_short_sk_prefix_not_redacted(self):
        """Short sk- prefixes (not real OpenAI keys) should not be redacted."""
        r = SecretRedactor()
        text = "sk-abc123"
        result = r.redact(text)
        assert result == text  # Should remain unchanged

    def test_redacts_aws_access_key(self):
        r = SecretRedactor()
        text = "AKIAIOSFODNN7EXAMPLE"
        result = r.redact(text)
        assert "AKIA" not in result
        assert "[REDACTED_AWS_ACCESS_KEY]" in result

    def test_redacts_bearer_token(self):
        r = SecretRedactor()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = r.redact(text)
        assert "eyJhbG" not in result
        assert "[REDACTED_BEARER_TOKEN]" in result

    def test_redacts_github_token(self):
        r = SecretRedactor()
        text = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = r.redact(text)
        assert "ghp_" not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result

    def test_redacts_api_key_param(self):
        r = SecretRedactor()
        text = "api_key=secret12345"
        result = r.redact(text)
        assert "secret12345" not in result
        assert "api_key=[REDACTED_API_KEY]" in result

    def test_redacts_password_param(self):
        r = SecretRedactor()
        text = 'password=mysecret123'
        result = r.redact(text)
        assert "mysecret123" not in result
        assert "password=[REDACTED_PASSWORD]" in result

    def test_redacts_connection_string_password(self):
        r = SecretRedactor()
        text = "postgresql://user:secretpass@localhost/db"
        result = r.redact(text)
        assert "secretpass" not in result
        assert "postgresql://user:[REDACTED_PASSWORD]@localhost/db" in result

    def test_redacts_private_key(self):
        r = SecretRedactor()
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        result = r.redact(text)
        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "[REDACTED_PRIVATE_KEY]" in result

    def test_no_false_positives_on_normal_text(self):
        r = SecretRedactor()
        text = "Hello world, this is a normal message with no secrets."
        result = r.redact(text)
        assert result == text

    def test_empty_text(self):
        r = SecretRedactor()
        assert r.redact("") == ""
        assert r.redact(None) is None  # type: ignore


class TestSecretRedactorDict:
    """Dictionary redaction tests."""

    def test_redact_dict_all_keys(self):
        r = SecretRedactor()
        key = "sk-" + "a" * 48
        data = {
            "message": f"The key is {key}",
            "status": "ok",
        }
        result = r.redact_dict(data)
        assert "[REDACTED_OPENAI_KEY]" in result["message"]
        assert result["status"] == "ok"

    def test_redact_dict_selective_keys(self):
        r = SecretRedactor()
        data = {
            "content": "api_key=secret123",
            "metadata": "api_key=should_stay",
        }
        result = r.redact_dict(data, keys_to_scan={"content"})
        assert "[REDACTED_API_KEY]" in result["content"]
        assert result["metadata"] == "api_key=should_stay"

    def test_redact_dict_nested(self):
        r = SecretRedactor()
        data = {
            "outer": {
                "inner": "password=secret123",
            },
        }
        result = r.redact_dict(data)
        assert "[REDACTED_PASSWORD]" in result["outer"]["inner"]

    def test_redact_dict_list_values(self):
        r = SecretRedactor()
        key = "sk-" + "a" * 48
        data = {
            "messages": [
                key,
                {"text": "password=foo"},
            ],
        }
        result = r.redact_dict(data)
        assert "[REDACTED_OPENAI_KEY]" in result["messages"][0]
        assert "[REDACTED_PASSWORD]" in result["messages"][1]["text"]


class TestSecretRedactorScan:
    """Scan-only tests."""

    def test_scan_finds_secrets(self):
        r = SecretRedactor()
        key = "sk-" + "a" * 48
        gh_token = "ghp_" + "x" * 36
        text = f"Keys: {key} and {gh_token}"
        findings = r.scan(text)
        names = [f[0] for f in findings]
        assert "openai_key" in names
        assert "github_token" in names

    def test_scan_returns_original_matches(self):
        r = SecretRedactor()
        key = "sk-" + "a" * 48
        text = key
        findings = r.scan(text)
        assert len(findings) == 1
        assert findings[0][0] == "openai_key"
        assert findings[0][1].startswith("sk-")


class TestSecretRedactorCustomPatterns:
    """Custom pattern tests."""

    def test_additional_patterns(self):
        custom = RedactionPattern(
            name="custom_token",
            regex=__import__("re").compile(r"token_[a-z0-9]{8}"),
            placeholder="[REDACTED_CUSTOM]",
        )
        r = SecretRedactor(additional_patterns=[custom])
        text = "token_abc12345"
        result = r.redact(text)
        assert "[REDACTED_CUSTOM]" in result

    def test_replace_default_patterns(self):
        custom = RedactionPattern(
            name="only_pattern",
            regex=__import__("re").compile(r"foo"),
            placeholder="bar",
        )
        r = SecretRedactor(patterns=[custom])
        assert r.pattern_names == ["only_pattern"]


class TestSecretRedactorSingleton:
    """Singleton convenience tests."""

    def test_default_redactor_singleton(self):
        r1 = get_default_redactor()
        r2 = get_default_redactor()
        assert r1 is r2

    def test_redact_function(self):
        key = "sk-" + "a" * 48
        text = key
        result = redact(text)
        assert "[REDACTED_OPENAI_KEY]" in result


class TestSecretRedactorPerformance:
    """Performance characteristics."""

    def test_large_text_performance(self):
        r = SecretRedactor()
        # 100KB of normal text + 1 secret
        key = "sk-" + "a" * 48
        text = "Hello world. " * 10000 + key
        result = r.redact(text)
        assert "[REDACTED_OPENAI_KEY]" in result
        assert len(result) < len(text)  # Should be shorter (redacted)
