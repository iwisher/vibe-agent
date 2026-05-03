"""Skills Guard - restricts skill operations and isolates sub-agent execution.

Prevents skills from:
- Accessing dangerous system operations
- Escaping their designated workspace
- Exfiltrating sensitive data
- Executing unapproved sub-agents
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class SkillRestriction(Enum):
    """Types of skill restrictions."""
    NONE = "none"           # No restrictions
    READ_ONLY = "read_only" # Can only read files, no writes
    SANDBOXED = "sandboxed" # Full sandbox with no system access
    DENY_ALL = "deny_all"   # Block all skill execution


@dataclass
class SkillGuardResult:
    """Result of skill guard check."""
    allowed: bool
    reason: str
    restriction_level: SkillRestriction


class SkillsGuard:
    """Guards skill execution and sub-agent operations."""

    # Dangerous operations that skills should never perform
    DANGEROUS_PATTERNS = [
        r"rm\s+-rf",
        r"sudo\s+",
        r"chmod\s+777",
        r"mkfs\.",
        r"dd\s+if=",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"__import__\s*\(",
        r"subprocess\.call",
        r"os\.system\s*\(",
        r"pty\.spawn",
        r"socket\.",
    ]

    # Sensitive paths that skills should not access
    SENSITIVE_PATHS = [
        "~/.ssh",
        "~/.aws",
        "~/.vibe",
        "/etc/passwd",
        "/etc/shadow",
        "/proc",
        "/sys",
        "/dev",
    ]

    # Allowed file extensions for skill operations
    ALLOWED_EXTENSIONS = {
        ".py", ".md", ".txt", ".json", ".yaml", ".yml",
        ".toml", ".cfg", ".ini", ".sh", ".bash",
    }

    def __init__(
        self,
        restriction_level: SkillRestriction = SkillRestriction.SANDBOXED,
        allowed_workspace: Optional[Path] = None,
        max_file_size_mb: float = 10.0,
    ):
        self.restriction_level = restriction_level
        self.allowed_workspace = allowed_workspace
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self._dangerous_regex = [re.compile(p, re.IGNORECASE) for p in self.DANGEROUS_PATTERNS]

    def check_skill_code(self, code: str, skill_name: str = "unknown") -> SkillGuardResult:
        """Check if skill code is safe to execute."""
        if self.restriction_level == SkillRestriction.DENY_ALL:
            return SkillGuardResult(
                allowed=False,
                reason="All skill execution is denied (DENY_ALL mode)",
                restriction_level=self.restriction_level,
            )

        if self.restriction_level == SkillRestriction.READ_ONLY:
            # Check for write operations
            write_patterns = [
                r"open\s*\([^)]*['\"]w",
                r"write_file",
                r"os\.mkdir",
                r"os\.makedirs",
                r"shutil\.copy",
                r"shutil\.move",
            ]
            for pattern in write_patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    return SkillGuardResult(
                        allowed=False,
                        reason=f"Write operation detected in read-only mode: {pattern}",
                        restriction_level=self.restriction_level,
                    )

        # Check for dangerous patterns
        for pattern in self._dangerous_regex:
            if pattern.search(code):
                return SkillGuardResult(
                    allowed=False,
                    reason=f"Dangerous pattern detected: {pattern.pattern}",
                    restriction_level=self.restriction_level,
                )

        return SkillGuardResult(
            allowed=True,
            reason="Skill code passed security checks",
            restriction_level=self.restriction_level,
        )

    def check_file_access(
        self,
        file_path: str,
        operation: str = "read",
    ) -> SkillGuardResult:
        """Check if a file access is allowed."""
        path = Path(file_path).expanduser().resolve()

        if self.restriction_level == SkillRestriction.DENY_ALL:
            return SkillGuardResult(
                allowed=False,
                reason="All file access denied (DENY_ALL mode)",
                restriction_level=self.restriction_level,
            )

        # Check sensitive paths
        for sensitive in self.SENSITIVE_PATHS:
            sensitive_path = Path(sensitive).expanduser().resolve()
            if str(path).startswith(str(sensitive_path)):
                return SkillGuardResult(
                    allowed=False,
                    reason=f"Access to sensitive path denied: {sensitive}",
                    restriction_level=self.restriction_level,
                )

        # Check workspace restriction
        if self.allowed_workspace:
            workspace = self.allowed_workspace.expanduser().resolve()
            try:
                path.relative_to(workspace)
            except ValueError:
                return SkillGuardResult(
                    allowed=False,
                    reason=f"Path {path} is outside allowed workspace {workspace}",
                    restriction_level=self.restriction_level,
                )

        # Check write restrictions
        if operation == "write" and self.restriction_level == SkillRestriction.READ_ONLY:
            return SkillGuardResult(
                allowed=False,
                reason="Write operations not allowed in READ_ONLY mode",
                restriction_level=self.restriction_level,
            )

        # Check file extension (only for existing files)
        if path.exists() and path.is_file() and path.suffix not in self.ALLOWED_EXTENSIONS:
            return SkillGuardResult(
                allowed=False,
                reason=f"File extension '{path.suffix}' not in allowed list",
                restriction_level=self.restriction_level,
            )

        # Check file size for reads
        if operation == "read" and path.exists() and path.is_file():
            if path.stat().st_size > self.max_file_size_bytes:
                return SkillGuardResult(
                    allowed=False,
                    reason=f"File size {path.stat().st_size} exceeds limit {self.max_file_size_bytes}",
                    restriction_level=self.restriction_level,
                )

        return SkillGuardResult(
            allowed=True,
            reason="File access permitted",
            restriction_level=self.restriction_level,
        )

    def check_subagent_spawn(
        self,
        agent_type: str,
        capabilities: list[str],
    ) -> SkillGuardResult:
        """Check if spawning a sub-agent is allowed."""
        if self.restriction_level == SkillRestriction.DENY_ALL:
            return SkillGuardResult(
                allowed=False,
                reason="Sub-agent spawning denied (DENY_ALL mode)",
                restriction_level=self.restriction_level,
            )

        if self.restriction_level == SkillRestriction.SANDBOXED:
            # In sandboxed mode, only allow restricted sub-agents
            dangerous_capabilities = {
                "terminal", "shell", "execute", "system", "network",
                "file_delete", "database_write", "email_send",
            }

            for cap in capabilities:
                if cap.lower() in dangerous_capabilities:
                    return SkillGuardResult(
                        allowed=False,
                        reason=f"Sub-agent capability '{cap}' not allowed in sandboxed mode",
                        restriction_level=self.restriction_level,
                    )

        return SkillGuardResult(
            allowed=True,
            reason="Sub-agent spawn permitted",
            restriction_level=self.restriction_level,
        )

    def check_network_access(
        self,
        url: str,
        method: str = "GET",
    ) -> SkillGuardResult:
        """Check if network access is allowed."""
        if self.restriction_level in (SkillRestriction.DENY_ALL, SkillRestriction.SANDBOXED):
            return SkillGuardResult(
                allowed=False,
                reason=f"Network access denied in {self.restriction_level.value} mode",
                restriction_level=self.restriction_level,
            )

        # Block internal/private IPs
        internal_patterns = [
            r"^http://(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)",
            r"^https?://.*\.internal",
            r"^https?://.*\.local",
        ]

        for pattern in internal_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return SkillGuardResult(
                    allowed=False,
                    reason=f"Access to internal URL denied: {url}",
                    restriction_level=self.restriction_level,
                )

        return SkillGuardResult(
            allowed=True,
            reason="Network access permitted",
            restriction_level=self.restriction_level,
        )

    def wrap_skill_execution(self, skill_func, *args, **kwargs):
        """Wrap skill execution with guard checks."""
        if self.restriction_level == SkillRestriction.DENY_ALL:
            raise PermissionError("Skill execution denied by SkillsGuard")

        # Pre-execution checks could go here
        return skill_func(*args, **kwargs)
