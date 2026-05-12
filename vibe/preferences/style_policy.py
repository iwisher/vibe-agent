"""Response style policy — user preferences for agent behavior and output format."""

from __future__ import annotations

from enum import Enum
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class Verbosity(str, Enum):
    TERSE = "terse"
    NORMAL = "normal"
    VERBOSE = "verbose"


class PlanFormat(str, Enum):
    BULLETS = "bullets"
    NUMBERED = "numbered"
    DAG = "dag"


class ConfirmThreshold(str, Enum):
    NEVER = "never"
    DESTRUCTIVE = "destructive"
    ALWAYS = "always"


class ResponseStylePolicy:
    """User preferences for agent response style.

    Mined from explicit commands and user corrections.
    """

    DOMAIN = "style"

    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()

    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)

    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)

    def set_verbosity(self, level: Verbosity) -> None:
        self._set_field("verbosity", level.value)

    def set_plan_format(self, fmt: PlanFormat) -> None:
        self._set_field("plan_format", fmt.value)

    def set_confirm_threshold(self, threshold: ConfirmThreshold) -> None:
        self._set_field("confirm_threshold", threshold.value)

    def set_show_commands(self, show: bool) -> None:
        self._set_field("show_commands_before_run", show)

    def _set_field(self, key: str, value: Any) -> None:
        """Update a style field, replacing any existing rule."""
        if self._policy is None:
            return
        self._policy.rules = [r for r in self._policy.rules if r.pattern != key]
        self._policy.add_rule(
            PreferenceRule(
                pattern=key,
                action="set",
                action_args={"value": value},
                source=PreferenceSource.EXPLICIT,
            )
        )
        self._save()

    def get_system_prompt_append(self) -> str:
        """Generate system prompt additions from style preferences."""
        if self._policy is None or not self._policy.enabled:
            return ""

        parts = []
        for rule in self._policy.get_enabled_rules():
            val = rule.action_args.get("value")
            if rule.pattern == "verbosity":
                if val == "terse":
                    parts.append("Be concise. Use minimal words. Avoid pleasantries.")
                elif val == "verbose":
                    parts.append("Be thorough. Explain reasoning step by step.")
            elif rule.pattern == "plan_format":
                parts.append(f"Format multi-step plans as {val}.")
            elif rule.pattern == "confirm_threshold":
                if val == "never":
                    parts.append("Never ask for confirmation. Just execute.")
                elif val == "destructive":
                    parts.append(
                        "Only ask for confirmation on destructive operations (delete, overwrite)."
                    )
            elif rule.pattern == "show_commands_before_run":
                if val:
                    parts.append("Always show the exact command before executing it.")

        return "\n".join(parts)

    def get_field(self, key: str, default: Any = None) -> Any:
        """Get a style field value."""
        if self._policy is None:
            return default
        for rule in self._policy.rules:
            if rule.pattern == key:
                return rule.action_args.get("value", default)
        return default
