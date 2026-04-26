"""Secret redaction utilities for preventing credential leakage.

Scans text for common secret patterns and replaces them with a redaction
placeholder before persistence or logging. This prevents API keys, tokens,
and passwords from being written to trace stores, logs, or eval databases.

Patterns supported:
- OpenAI API keys (sk-...)
- AWS access keys (AKIA...)
- AWS secret keys
- Generic bearer tokens
- GitHub personal access tokens (ghp_...)
- Generic API keys (api_key=..., apikey=...)
- Password fields (password=..., passwd=...)
- Private keys (-----BEGIN RSA/EC/DSA PRIVATE KEY-----)
- Connection strings with embedded passwords
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RedactionPattern:
    """A single secret detection pattern."""
    name: str
    regex: re.Pattern
    placeholder: str


# Pre-compiled patterns for performance
_DEFAULT_PATTERNS: list[RedactionPattern] = [
    RedactionPattern(
        name="openai_key",
        regex=re.compile(r"sk-[a-zA-Z0-9]{48}"),
        placeholder="[REDACTED_OPENAI_KEY]",
    ),
    RedactionPattern(
        name="aws_access_key",
        regex=re.compile(r"AKIA[0-9A-Z]{16}"),
        placeholder="[REDACTED_AWS_ACCESS_KEY]",
    ),
    # AWS secret keys: 40 base64 chars, but only when preceded by "AWS" or "Secret" context
    # to avoid matching OpenAI keys (sk-...) or random base64 strings
    RedactionPattern(
        name="aws_secret_key",
        regex=re.compile(r"(?:AWS|Secret|secret)[\s=:]+([0-9a-zA-Z/+]{40})"),
        placeholder=r"\1[REDACTED_AWS_SECRET]",
    ),
    RedactionPattern(
        name="bearer_token",
        regex=re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+"),
        placeholder="[REDACTED_BEARER_TOKEN]",
    ),
    RedactionPattern(
        name="github_token",
        regex=re.compile(r"ghp_[a-zA-Z0-9]{36}"),
        placeholder="[REDACTED_GITHUB_TOKEN]",
    ),
    RedactionPattern(
        name="api_key_param",
        regex=re.compile(r"(api[_-]?key\s*[:=]\s*)[a-zA-Z0-9_\-]+", re.IGNORECASE),
        placeholder=r"\1[REDACTED_API_KEY]",
    ),
    RedactionPattern(
        name="password_param",
        regex=re.compile(r"(password\s*[:=]\s*)[^\s&\"\']+", re.IGNORECASE),
        placeholder=r"\1[REDACTED_PASSWORD]",
    ),
    RedactionPattern(
        name="private_key",
        regex=re.compile(
            r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----[\s\S]*?-----END \1 PRIVATE KEY-----"
        ),
        placeholder="[REDACTED_PRIVATE_KEY]",
    ),
    RedactionPattern(
        name="connection_string_password",
        regex=re.compile(r"(://[^:]*:)([^@]*)@"),
        placeholder=r"\1[REDACTED_PASSWORD]@",
    ),
]


class SecretRedactor:
    """Redacts secrets from text before persistence.

    Usage:
        redactor = SecretRedactor()
        clean = redactor.redact("The key is sk-abc123... and password=secret")
        # clean == "The key is [REDACTED_OPENAI_KEY] and password=[REDACTED_PASSWORD]"
    """

    def __init__(
        self,
        patterns: Optional[list[RedactionPattern]] = None,
        additional_patterns: Optional[list[RedactionPattern]] = None,
    ):
        """Initialize with default, custom, or combined patterns.

        Args:
            patterns: Full pattern list (replaces defaults). If None, uses defaults.
            additional_patterns: Extra patterns appended to defaults.
        """
        if patterns is not None:
            self._patterns = patterns
        elif additional_patterns is not None:
            self._patterns = _DEFAULT_PATTERNS + additional_patterns
        else:
            self._patterns = _DEFAULT_PATTERNS

    def redact(self, text: str) -> str:
        """Redact all known secret patterns from text.

        Returns the redacted string. If text is empty or None, returns as-is.
        """
        if not text:
            return text
        result = text
        for pattern in self._patterns:
            result = pattern.regex.sub(pattern.placeholder, result)
        return result

    def redact_dict(self, data: dict, keys_to_scan: Optional[set[str]] = None) -> dict:
        """Redact secrets in dictionary values.

        Only scans string values. By default scans all keys, or pass
        keys_to_scan to limit which keys are checked.

        Args:
            data: Dictionary to redact (e.g., message dict, tool result dict).
            keys_to_scan: Set of keys to check. If None, checks all string values.

        Returns:
            New dict with redacted values. Original dict is not modified.
        """
        result = {}
        for key, value in data.items():
            if keys_to_scan is not None and key not in keys_to_scan:
                result[key] = value
            elif isinstance(value, str):
                result[key] = self.redact(value)
            elif isinstance(value, dict):
                result[key] = self.redact_dict(value, keys_to_scan)
            elif isinstance(value, list):
                result[key] = [
                    self.redact(v) if isinstance(v, str) else
                    self.redact_dict(v, keys_to_scan) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result

    def scan(self, text: str) -> list[tuple[str, str]]:
        """Scan text for secrets without redacting.

        Returns list of (pattern_name, matched_text) tuples.
        Useful for logging what was found without modifying the text.
        """
        if not text:
            return []
        findings: list[tuple[str, str]] = []
        for pattern in self._patterns:
            for match in pattern.regex.finditer(text):
                findings.append((pattern.name, match.group(0)))
        return findings

    @property
    def pattern_names(self) -> list[str]:
        """Return names of all configured patterns."""
        return [p.name for p in self._patterns]


# Convenience singleton for common usage
_default_redactor: Optional[SecretRedactor] = None


def get_default_redactor() -> SecretRedactor:
    """Return the default SecretRedactor singleton."""
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = SecretRedactor()
    return _default_redactor


def redact(text: str) -> str:
    """Redact secrets using the default redactor."""
    return get_default_redactor().redact(text)
