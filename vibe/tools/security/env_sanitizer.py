"""Environment sanitization for vibe-agent.

Blocks PATH overrides, strips dangerous env keys, limits env value size.
"""

import os
import re
from typing import Optional

# Dangerous env key prefixes that should be blocked
DANGEROUS_ENV_PREFIXES: tuple[str, ...] = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "AUTH",
    "ACCESS_KEY",
    "AWS_SECRET",
    "GITHUB_TOKEN",
    "SLACK_TOKEN",
)

# Allowed env vars for shell transports (locale, color, terminal)
ALLOWED_SHELL_ENV: set[str] = {
    "LANG", "LC_ALL", "LC_CTYPE", "LC_NUMERIC", "LC_TIME",
    "LC_COLLATE", "LC_MONETARY", "LC_MESSAGES", "LC_PAPER",
    "LC_NAME", "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT",
    "LC_IDENTIFICATION", "TERM", "TERM_PROGRAM", "COLORTERM",
    "NO_COLOR", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE",
    "HOME", "USER", "LOGNAME", "SHELL", "PWD", "OLDPWD",
    "PATH", "EDITOR", "VISUAL", "PAGER", "LESS", "MORE",
    "TZ", "TIMEZONE",
}

# Max env value size (32KB from OpenClaw)
MAX_ENV_VALUE_SIZE: int = 32 * 1024

# Base64 pattern for credential detection
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$")


class EnvSanitizer:
    """Sanitizes environment variables before subprocess execution."""

    def __init__(self, allowed_vars: Optional[set[str]] = None):
        self.allowed_vars = allowed_vars or ALLOWED_SHELL_ENV

    def sanitize(self, env: dict[str, str] | None = None) -> dict[str, str]:
        """Sanitize environment variables.

        Returns a new dict with only safe variables.
        """
        if env is None:
            env = dict(os.environ)

        sanitized: dict[str, str] = {}
        for key, value in env.items():
            # Check if allowed
            if key not in self.allowed_vars:
                # Check for dangerous prefixes (anywhere in key)
                if any(p in key.upper() for p in DANGEROUS_ENV_PREFIXES):
                    continue  # Strip dangerous env var
                # Check for base64-encoded credentials
                if _BASE64_RE.match(value):
                    continue  # Strip potential base64 credential

            # Check value size
            if len(value) > MAX_ENV_VALUE_SIZE:
                continue  # Strip oversized value

            sanitized[key] = value

        return sanitized

    def block_path_override(self, env: dict[str, str]) -> dict[str, str]:
        """Block PATH overrides from request-scoped env."""
        if "PATH" in env:
            # Keep original PATH, reject override
            original_path = os.environ.get("PATH", "")
            env["PATH"] = original_path
        return env

    def strip_for_shell(self, env: dict[str, str]) -> dict[str, str]:
        """Strip env to only locale/color/terminal vars for shell transports."""
        return {k: v for k, v in env.items() if k in ALLOWED_SHELL_ENV}


def sanitize_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Convenience function to sanitize environment."""
    sanitizer = EnvSanitizer()
    return sanitizer.sanitize(env)
