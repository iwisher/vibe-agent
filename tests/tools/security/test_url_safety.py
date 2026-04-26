"""Tests for URL safety / SSRF protection."""

import pytest

from vibe.tools.security.url_safety import (
    URLSafetyChecker,
    URLSafetyError,
    check_url_safe,
)


class TestURLSafetyChecker:
    """Test URLSafetyChecker."""

    def test_blocks_metadata_ip(self):
        checker = URLSafetyChecker()
        with pytest.raises(URLSafetyError) as exc:
            checker.check_url("http://169.254.169.254/latest/meta-data/")
        assert exc.value.reason == "blocked_ip"

    def test_blocks_private_ip(self):
        checker = URLSafetyChecker()
        with pytest.raises(URLSafetyError) as exc:
            checker.check_url("http://192.168.1.1/admin")
        assert exc.value.reason == "private_network"

    def test_blocks_loopback(self):
        checker = URLSafetyChecker()
        with pytest.raises(URLSafetyError) as exc:
            checker.check_url("http://127.0.0.1:8080")
        assert exc.value.reason == "private_network"

    def test_blocks_metadata_hostname(self):
        checker = URLSafetyChecker()
        with pytest.raises(URLSafetyError) as exc:
            checker.check_url("http://metadata.google.internal/")
        assert exc.value.reason == "blocked_hostname"

    def test_allows_public_url(self):
        checker = URLSafetyChecker()
        checker.check_url("https://example.com/api")  # Should not raise

    def test_allows_private_when_configured(self):
        checker = URLSafetyChecker(allow_private=True)
        checker.check_url("http://192.168.1.1/admin")  # Should not raise

    def test_blocks_invalid_scheme(self):
        checker = URLSafetyChecker()
        with pytest.raises(URLSafetyError) as exc:
            checker.check_url("ftp://example.com")
        assert exc.value.reason == "invalid_scheme"

    def test_check_redirect(self):
        checker = URLSafetyChecker()
        with pytest.raises(URLSafetyError):
            checker.check_redirect("https://example.com", "http://169.254.169.254/")

    def test_convenience_function(self):
        with pytest.raises(URLSafetyError):
            check_url_safe("http://127.0.0.1")
