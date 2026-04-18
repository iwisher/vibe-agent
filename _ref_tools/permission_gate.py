"""Permission gates for tool execution."""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Callable


class PermissionLevel(Enum):
    """Permission levels for tool execution."""
    ALLOW = auto()
    ASK = auto()
    DENY = auto()


class PermissionDecision(Enum):
    """User decision on permission prompt."""
    ALLOW = auto()
    ALLOW_ONCE = auto()
    DENY = auto()
    DENY_ALWAYS = auto()


@dataclass
class PermissionRule:
    """A permission rule for a tool."""
    tool_pattern: str  # Can be wildcard like "bash_*" or "*"
    level: PermissionLevel
    conditions: Optional[dict] = None  # Additional conditions


class PermissionGate:
    """Manages permissions for tool execution."""

    DANGEROUS_PATTERNS = [
        "rm -rf /",
        "rm -rf ~",
        "rm -rf /*",
        "> /dev/sda",
        "dd if=/dev/zero",
        "mkfs",
        ":(){ :|:& };:",  # Fork bomb
        "chmod -R 777 /",
        "mv / /dev/null",
    ]

    def __init__(self):
        self._rules: dict = {}
        self._session_allowlist: set = set()
        self._session_denylist: set = set()
        self._ask_callback: Optional[Callable[[str, str], PermissionDecision]] = None

    def set_ask_callback(self, callback: Callable[[str, str], PermissionDecision]):
        """Set callback for asking user permission."""
        self._ask_callback = callback

    def set_permission(self, tool_name: str, level: PermissionLevel):
        """Set permission level for a tool."""
        self._rules[tool_name] = level

    def check_permission(self, tool_name: str, params: dict) -> tuple[bool, Optional[str]]:
        """Check if tool execution is permitted.

        Returns:
            (allowed, error_message)
        """
        # Check session allowlist
        if tool_name in self._session_allowlist:
            return True, None

        # Check session denylist
        if tool_name in self._session_denylist:
            return False, f"Tool '{tool_name}' is in session denylist"

        # Get permission level
        level = self._rules.get(tool_name, PermissionLevel.ASK)

        if level == PermissionLevel.DENY:
            return False, f"Tool '{tool_name}' is denied by policy"

        if level == PermissionLevel.ALLOW:
            # Still check for dangerous patterns
            if self._is_dangerous(tool_name, params):
                return False, "Command blocked: dangerous pattern detected"
            return True, None

        # PermissionLevel.ASK - need user confirmation
        if level == PermissionLevel.ASK:
            description = self._describe_request(tool_name, params)

            # Auto-deny dangerous patterns
            if self._is_dangerous(tool_name, params):
                return False, "Command blocked: dangerous pattern detected"

            if self._ask_callback:
                decision = self._ask_callback(tool_name, description)

                if decision == PermissionDecision.ALLOW:
                    self._session_allowlist.add(tool_name)
                    return True, None
                elif decision == PermissionDecision.ALLOW_ONCE:
                    return True, None
                elif decision == PermissionDecision.DENY_ALWAYS:
                    self._session_denylist.add(tool_name)
                    return False, f"Tool '{tool_name}' denied"
                else:  # DENY
                    return False, f"Tool '{tool_name}' denied by user"
            else:
                # No callback, default to deny
                return False, f"Tool '{tool_name}' requires permission but no callback configured"

        return True, None

    def _is_dangerous(self, tool_name: str, params: dict) -> bool:
        """Check if the operation is dangerous."""
        # Check bash commands
        if "bash" in tool_name.lower() or tool_name == "Bash":
            command = params.get("command", "")
            return self._is_dangerous_command(command)

        # Check file write operations
        if tool_name in ["WriteFile", "EditFile"]:
            path = params.get("path", "")
            if self._is_sensitive_path(path):
                return True

        return False

    def _is_dangerous_command(self, command: str) -> bool:
        """Check if a bash command is dangerous."""
        command_lower = command.lower().strip()

        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in command_lower:
                return True

        # Additional checks
        dangerous_keywords = ["sudo", "su -", "passwd", " Shadow"]
        for keyword in dangerous_keywords:
            if keyword in command_lower:
                return True

        return False

    def _is_sensitive_path(self, path: str) -> bool:
        """Check if a path is sensitive."""
        sensitive = [
            "/etc/passwd",
            "/etc/shadow",
            "/etc/sudoers",
            "/.ssh",
            "/.bashrc",
            "/.zshrc",
            "/.profile",
        ]
        path_lower = path.lower()
        for s in sensitive:
            if s in path_lower:
                return True
        return False

    def _describe_request(self, tool_name: str, params: dict) -> str:
        """Create a human-readable description of the request."""
        if tool_name == "Bash":
            return f"Execute command: {params.get('command', '')}"
        elif tool_name == "ReadFile":
            return f"Read file: {params.get('path', '')}"
        elif tool_name == "WriteFile":
            return f"Write to file: {params.get('path', '')}"
        else:
            return f"Execute {tool_name} with params: {params}"