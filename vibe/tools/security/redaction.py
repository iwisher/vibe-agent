"""Secret redaction for vibe-agent.

Redacts sensitive patterns from text before logging or sending to LLM.
"""

import re

# Secret patterns (40+ regex patterns)
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("sk_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("github_token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("github_oauth", re.compile(r"gho_[a-zA-Z0-9]{36}")),
    ("github_app", re.compile(r"ghu_[a-zA-Z0-9]{36}")),
    ("github_refresh", re.compile(r"ghr_[a-zA-Z0-9]{36}")),
    ("slack_token", re.compile(r"xox[baprs]-[a-zA-Z0-9-]+")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Z0-9/]+")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key", re.compile(r"[0-9a-zA-Z/+]{40}")),
    ("stripe_key", re.compile(r"sk_(live|test)_[0-9a-zA-Z]{24,}")),
    ("stripe_publishable", re.compile(r"pk_(live|test)_[0-9a-zA-Z]{24,}")),
    ("jwt", re.compile(r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*")),
    ("bearer_token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.=]+")),
    ("basic_auth", re.compile(r"Basic\s+[a-zA-Z0-9+/=]+")),
    ("api_key", re.compile(r"api[_-]?key\s*[:=]\s*[a-zA-Z0-9_-]{16,}", re.IGNORECASE)),
    ("password", re.compile(r"password\s*[:=]\s*[^\s\"\']{8,}", re.IGNORECASE)),
    ("secret", re.compile(r"secret\s*[:=]\s*[^\s\"\']{8,}", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("discord_token", re.compile(r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}")),
    ("discord_webhook", re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+")),
    ("url_token", re.compile(r"[?&](access_token|token|code|api_key|apikey|key|secret|password)=[^&\s]{8,}", re.IGNORECASE)),
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("phone", re.compile(r"\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")),
    ("discord_mention", re.compile(r"<@!?\d{18}>|<#\d{18}>|<@&\d{18}>")),
]

# Replacement string
REDACTED = "[REDACTED]"


def redact_sensitive_text(text: str, replacement: str = REDACTED) -> str:
    """Redact sensitive patterns from text.

    Returns redacted text.
    """
    if not text:
        return text

    result = text
    for name, pattern in SECRET_PATTERNS:
        result = pattern.sub(replacement, result)

    return result


def redact_url_query_params(url: str, replacement: str = REDACTED) -> str:
    """Redact sensitive query parameters from URL.

    Strips access_token, code, api_key, etc.
    """
    import re

    # Use regex to avoid urllib.parse issues with malformed URLs
    # Pattern: ?key=value or &key=value
    sensitive_keys = {"access_token", "token", "code", "api_key", "apikey",
                      "key", "secret", "password", "client_secret"}

    def replace_param(match):
        prefix = match.group(1)  # ? or &
        key = match.group(2)     # parameter name
        match.group(3)   # parameter value
        if key.lower() in sensitive_keys:
            return f"{prefix}{key}={replacement}"
        return match.group(0)

    # Match query parameters: ?key=value or &key=value
    pattern = re.compile(r'([?&])([^=]+)=([^&\s]+)')
    return pattern.sub(replace_param, url)


def redact_url_userinfo(url: str) -> str:
    """Strip userinfo from URL (e.g., http://user:pass@host)."""
    # Use regex to avoid urllib.parse issues with malformed URLs
    import re
    # Pattern: scheme://user:pass@host -> scheme://host
    pattern = re.compile(r'(https?://)(?:[^@]+@)([^/]+)')
    return pattern.sub(r'\1\2', url)


def redact_all(text: str) -> str:
    """Apply all redaction methods."""
    text = redact_sensitive_text(text)
    # Try to redact URLs in text
    url_pattern = re.compile(r"https?://[^\s\"\']+")
    for match in url_pattern.findall(text):
        redacted_url = redact_url_userinfo(match)
        redacted_url = redact_url_query_params(redacted_url)
        text = text.replace(match, redacted_url)
    return text
