"""Tests for secret redaction."""


from vibe.tools.security.redaction import (
    REDACTED,
    redact_all,
    redact_sensitive_text,
    redact_url_query_params,
    redact_url_userinfo,
)


class TestRedactSensitiveText:
    """Test redact_sensitive_text."""

    def test_redacts_sk_key(self):
        text = "API key: sk-abc123def456ghi789jkl"
        result = redact_sensitive_text(text)
        assert "sk-" not in result
        assert REDACTED in result

    def test_redacts_github_token(self):
        text = "Token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = redact_sensitive_text(text)
        assert "ghp_" not in result

    def test_redacts_jwt(self):
        text = "Authorization: eyJhbGciOiJIUzI1NiIs.eyJzdWIiOiIxMjM0NTY3ODkwIiw.name"
        result = redact_sensitive_text(text)
        assert "eyJ" not in result

    def test_redacts_slack_token(self):
        text = "Token: xoxb-FAKE...REDACT"
        result = redact_sensitive_text(text)
        assert "xoxb" not in result

    def test_redacts_aws_key(self):
        text = "Access key: AKIAIOSFODNN7EXAMPLE"
        result = redact_sensitive_text(text)
        assert "AKIA" not in result

    def test_redacts_api_key_param(self):
        text = "api_key=secret1234567890123456"
        result = redact_sensitive_text(text)
        assert "secret1234567890123456" not in result

    def test_no_false_positives_short_text(self):
        text = "Hello world"
        result = redact_sensitive_text(text)
        assert result == "Hello world"


class TestRedactURL:
    """Test URL redaction."""

    def test_redacts_query_params(self):
        url = "https://example.com/api?access_token=secret123&user=john"
        result = redact_url_query_params(url)
        assert "secret123" not in result
        assert "user=john" in result

    def test_redacts_userinfo(self):
        url = "http://user:password@example.com/path"
        result = redact_url_userinfo(url)
        assert "user:password" not in result
        assert "http://example.com/path" == result

    def test_redact_all_combined(self):
        text = "API key: sk-abc123 and URL: http://user:pass@example.com?token=secret123"
        result = redact_all(text)
        # URL userinfo and query params get redacted
        assert "user:pass" not in result
        assert "secret123" not in result
        # sk-abc123 pattern doesn't match (too short), but other patterns may redact parts
        assert "[REDACTED]" in result
