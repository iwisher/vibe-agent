"""Constraint hooks and pipeline for the harness."""

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from vibe.tools.tool_system import ToolResult


class HookStage(Enum):
    PRE_VALIDATE = auto()
    PRE_MODIFY = auto()
    PRE_ALLOW = auto()
    POST_EXECUTE = auto()
    POST_FIX = auto()


@dataclass
class HookContext:
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class HookSeverity(str, Enum):
    """Severity levels for hook outcomes."""

    BLOCK = "block"
    WARN = "warn"
    ALLOW = "allow"


@dataclass
class HookOutcome:
    allow: bool
    reason: str
    severity: HookSeverity = HookSeverity.ALLOW
    warnings: list[str] = field(default_factory=list)
    modified_arguments: dict[str, Any] = field(default_factory=dict)
    modified_result: ToolResult | None = None


ConstraintHook = Callable[[HookContext], HookOutcome]


class HookPipeline:
    """Ordered pipeline of constraint hooks."""

    def __init__(self):
        self._stages: dict[HookStage, list[ConstraintHook]] = {
            stage: [] for stage in HookStage
        }

    def add_hook(self, stage: HookStage, hook: ConstraintHook) -> None:
        self._stages[stage].append(hook)

    def run_pre_hooks(self, tool_name: str, arguments: dict[str, Any]) -> HookOutcome:
        """Run PRE_VALIDATE, PRE_MODIFY, PRE_ALLOW hooks in order.

        Rules:
        - First BLOCK wins: any hook returns severity=BLOCK → deny immediately
        - All WARN accumulate: collect all warnings, allow execution
        - Modified arguments compose: each hook can transform arguments; transformations chain
        - If a hook raises an exception, treat as BLOCK (fail-closed)
        """
        context = HookContext(tool_name=tool_name, arguments=arguments)
        current_args = dict(arguments)
        all_warnings: list[str] = []

        for stage in (HookStage.PRE_VALIDATE, HookStage.PRE_MODIFY, HookStage.PRE_ALLOW):
            for hook in self._stages[stage]:
                try:
                    outcome = hook(context)
                except Exception as exc:
                    # Fail-closed: hook exception = block
                    return HookOutcome(
                        allow=False,
                        reason=f"Hook crashed in {stage.name}: {exc}",
                        severity=HookSeverity.BLOCK,
                    )

                # First block wins
                if outcome.severity == HookSeverity.BLOCK or not outcome.allow:
                    return HookOutcome(
                        allow=False,
                        reason=outcome.reason,
                        severity=HookSeverity.BLOCK,
                        warnings=all_warnings + outcome.warnings,
                        modified_arguments=current_args,
                    )

                # Accumulate warnings
                if outcome.warnings:
                    all_warnings.extend(outcome.warnings)

                if outcome.modified_arguments:
                    current_args.update(outcome.modified_arguments)
                    context.arguments = current_args

        # Propagate WARN severity if any warnings accumulated
        final_severity = HookSeverity.WARN if all_warnings else HookSeverity.ALLOW
        return HookOutcome(
            allow=True,
            reason="ok",
            severity=final_severity,
            warnings=all_warnings,
            modified_arguments=current_args,
        )

    def run_post_hooks(
        self, tool_name: str, arguments: dict[str, Any], result: ToolResult
    ) -> ToolResult:
        """Run POST_EXECUTE and POST_FIX hooks in order.

        Rules:
        - First BLOCK wins: any hook returns severity=BLOCK → reject result
        - Modified results compose: each hook can transform results
        - If a hook raises an exception, treat as BLOCK (fail-closed)
        """
        context = HookContext(tool_name=tool_name, arguments=arguments, result=result)
        current_result = result

        for stage in (HookStage.POST_EXECUTE, HookStage.POST_FIX):
            for hook in self._stages[stage]:
                try:
                    outcome = hook(context)
                except Exception as exc:
                    # Fail-closed: hook exception = block
                    return ToolResult(
                        success=False,
                        content=None,
                        error=f"Post-hook crashed in {stage.name}: {exc}",
                    )

                if outcome.severity == HookSeverity.BLOCK or not outcome.allow:
                    return ToolResult(
                        success=False,
                        content=None,
                        error=f"Post-hook rejected: {outcome.reason}",
                    )
                if outcome.modified_result is not None:
                    current_result = outcome.modified_result
                    context.result = current_result

        return current_result


# Built-in hooks

def permission_gate_hook(
    destructive_tools: list[str] | None = None,
) -> ConstraintHook:
    """Blocks destructive tools unless explicitly allowed in metadata."""
    blocked = set(destructive_tools or ["write_file", "bash"])

    def hook(context: HookContext) -> HookOutcome:
        if context.tool_name in blocked and not context.metadata.get("user_approved"):
            return HookOutcome(
                allow=False,
                reason=f"Destructive tool '{context.tool_name}' requires user approval.",
                severity=HookSeverity.BLOCK,
            )
        return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

    return hook


def policy_hook(
    blocked_commands: list[str] | None = None,
) -> ConstraintHook:
    """Blocks specific bash commands or tool arguments.

    Handles shell metacharacters and enforces proper word boundaries.
    """
    blocked = set(blocked_commands or ["curl | bash", "rm -rf /", "sudo", "su -"])

    # Characters that act as word boundaries in shell commands
    _BOUNDARY_CHARS = set(" \t\n;|&()<>\"'`$!#*?[]{}=+-%~\\")

    def _find_whole_word(text: str, word: str, start: int = 0) -> int:
        """Find whole word occurrence. Returns position or -1.

        A whole word must not have alphanumeric/_ on either side.
        """
        pos = start
        while True:
            idx = text.lower().find(word.lower(), pos)
            if idx == -1:
                return -1

            # Check left boundary
            left_ok = idx == 0 or text[idx - 1] in _BOUNDARY_CHARS
            # Check right boundary
            right_ok = idx + len(word) >= len(text) or text[idx + len(word)] in _BOUNDARY_CHARS

            if left_ok and right_ok:
                return idx
            pos = idx + 1

    def hook(context: HookContext) -> HookOutcome:
        if context.tool_name == "bash":
            command = context.arguments.get("command", "")
            for b in blocked:
                if " " in b:
                    # Multi-word: check words appear in sequence as whole words
                    words = b.split()
                    last_pos = 0
                    found = True
                    for word in words:
                        idx = _find_whole_word(command, word, last_pos)
                        if idx == -1:
                            found = False
                            break
                        last_pos = idx + len(word)
                    if found:
                        return HookOutcome(
                            allow=False,
                            reason=f"Policy violation: blocked pattern '{b}' in command.",
                            severity=HookSeverity.BLOCK,
                        )
                else:
                    # Single-word: whole word match with shell delimiter support
                    if _find_whole_word(command, b, 0) != -1:
                        return HookOutcome(
                            allow=False,
                            reason=f"Policy violation: blocked pattern '{b}' in command.",
                            severity=HookSeverity.BLOCK,
                        )
        return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

    return hook


# Security integration hooks

def path_traversal_hook(allowed_paths: list[str] | None = None) -> ConstraintHook:
    """Blocks path traversal attempts in file operations.

    Integrates with security module to prevent directory escape attacks.
    """
    allowed = allowed_paths or ["."]

    def hook(context: HookContext) -> HookOutcome:
        if context.tool_name not in ("read_file", "write_file", "file_exists"):
            return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

        path_arg = context.arguments.get("path", "")
        if not path_arg:
            return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

        # Check for path traversal patterns
        normalized = os.path.normpath(path_arg)
        if ".." in path_arg or normalized.startswith(".."):
            return HookOutcome(
                allow=False,
                reason=f"Path traversal blocked: '{path_arg}'",
                severity=HookSeverity.BLOCK,
            )

        # Check if within allowed paths
        abs_path = os.path.abspath(normalized)
        for allowed_path in allowed:
            allowed_abs = os.path.abspath(allowed_path)
            if abs_path.startswith(allowed_abs):
                return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

        return HookOutcome(
            allow=False,
            reason=f"Path outside allowed directories: '{path_arg}'",
            severity=HookSeverity.BLOCK,
        )

    return hook


def file_size_hook(max_size_mb: float = 10.0) -> ConstraintHook:
    """Blocks file operations that exceed size limits.

    Prevents resource exhaustion from oversized file operations.
    """
    max_bytes = max_size_mb * 1024 * 1024

    def hook(context: HookContext) -> HookOutcome:
        if context.tool_name not in ("read_file", "write_file"):
            return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

        # For write operations, check content size
        if context.tool_name == "write_file":
            content = context.arguments.get("content", "")
            if len(content.encode("utf-8")) > max_bytes:
                return HookOutcome(
                    allow=False,
                    reason=f"File size exceeds limit of {max_size_mb}MB",
                    severity=HookSeverity.BLOCK,
                )

        # For read operations, check existing file size
        if context.tool_name == "read_file":
            path = context.arguments.get("path", "")
            try:
                if os.path.exists(path) and os.path.getsize(path) > max_bytes:
                    return HookOutcome(
                        allow=False,
                        reason=f"File size exceeds limit of {max_size_mb}MB",
                        severity=HookSeverity.BLOCK,
                    )
            except OSError:
                pass

        return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

    return hook


def network_policy_hook(allow_network: bool = False) -> ConstraintHook:
    """Blocks network-related tools unless explicitly allowed.

    Security hook to prevent unauthorized network access.
    """
    network_tools = {"curl", "wget", "fetch", "download", "http_request"}

    def hook(context: HookContext) -> HookOutcome:
        if not allow_network and context.tool_name in network_tools:
            return HookOutcome(
                allow=False,
                reason=f"Network tool '{context.tool_name}' blocked by policy",
                severity=HookSeverity.BLOCK,
            )
        return HookOutcome(allow=True, reason="ok", severity=HookSeverity.ALLOW)

    return hook


def create_security_pipeline(
    allowed_paths: list[str] | None = None,
    max_file_size_mb: float = 10.0,
    allow_network: bool = False,
    blocked_commands: list[str] | None = None,
    destructive_tools: list[str] | None = None,
) -> HookPipeline:
    """Create a pre-configured security pipeline with all security hooks.

    Integrates existing security modules into harness constraints:
    - Path traversal protection
    - File size limits
    - Network policy enforcement
    - Command blocking
    - Permission gating for destructive tools
    """
    pipeline = HookPipeline()

    # PRE_VALIDATE stage: Check permissions and policies
    pipeline.add_hook(HookStage.PRE_VALIDATE, permission_gate_hook(destructive_tools))
    pipeline.add_hook(HookStage.PRE_VALIDATE, policy_hook(blocked_commands))
    pipeline.add_hook(HookStage.PRE_VALIDATE, path_traversal_hook(allowed_paths))
    pipeline.add_hook(HookStage.PRE_VALIDATE, network_policy_hook(allow_network))

    # PRE_MODIFY stage: Enforce size limits
    pipeline.add_hook(HookStage.PRE_MODIFY, file_size_hook(max_file_size_mb))

    return pipeline
