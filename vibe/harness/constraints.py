"""Constraint hooks and pipeline for the harness."""

import re
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


@dataclass
class HookOutcome:
    allow: bool
    reason: str
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
        """Run PRE_VALIDATE, PRE_MODIFY, PRE_ALLOW hooks in order."""
        context = HookContext(tool_name=tool_name, arguments=arguments)
        current_args = dict(arguments)

        for stage in (HookStage.PRE_VALIDATE, HookStage.PRE_MODIFY, HookStage.PRE_ALLOW):
            for hook in self._stages[stage]:
                outcome = hook(context)
                if not outcome.allow:
                    return outcome
                if outcome.modified_arguments:
                    current_args.update(outcome.modified_arguments)
                    context.arguments = current_args

        return HookOutcome(allow=True, reason="ok", modified_arguments=current_args)

    def run_post_hooks(
        self, tool_name: str, arguments: dict[str, Any], result: ToolResult
    ) -> ToolResult:
        """Run POST_EXECUTE and POST_FIX hooks in order."""
        context = HookContext(tool_name=tool_name, arguments=arguments, result=result)
        current_result = result

        for stage in (HookStage.POST_EXECUTE, HookStage.POST_FIX):
            for hook in self._stages[stage]:
                outcome = hook(context)
                if not outcome.allow:
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
            )
        return HookOutcome(allow=True, reason="ok")

    return hook


def policy_hook(
    blocked_commands: list[str] | None = None,
) -> ConstraintHook:
    """Blocks specific bash commands or tool arguments."""
    blocked = set(blocked_commands or ["curl | bash", "rm -rf /", "sudo", "su -"])

    def hook(context: HookContext) -> HookOutcome:
        if context.tool_name == "bash":
            command = context.arguments.get("command", "")
            for b in blocked:
                # Multi-word patterns: exact substring match
                if " " in b:
                    if b in command:
                        return HookOutcome(
                            allow=False,
                            reason=f"Policy violation: blocked pattern '{b}' in command.",
                        )
                else:
                    # Single-word patterns: whole-word match to avoid false positives
                    if re.search(rf"\b{re.escape(b)}\b", command):
                        return HookOutcome(
                            allow=False,
                            reason=f"Policy violation: blocked pattern '{b}' in command.",
                        )
        return HookOutcome(allow=True, reason="ok")

    return hook
