"""Response style policy — controls verbosity, plan format, confirmation threshold."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class Verbosity(str, Enum):
    """Response verbosity levels."""

    TERSE = "terse"
    NORMAL = "normal"
    VERBOSE = "verbose"


class PlanFormat(str, Enum):
    """Plan presentation formats."""

    BULLETS = "bullets"
    NUMBERED = "numbered"
    DAG = "dag"


class ConfirmThreshold(str, Enum):
    """When to ask for user confirmation before acting."""

    NEVER = "never"
    DESTRUCTIVE = "destructive"
    ALWAYS = "always"


class ResponseStylePolicy:
    """Policy for controlling response style preferences.

    Stores verbosity, plan format, confirmation threshold, and command visibility.
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

    def _set_field(
        self,
        key: str,
        value: Any,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set a single field value, overwriting any existing rule for that key."""
        rule = PreferenceRule(
            pattern=key,
            action="set_value",
            action_args={"value": value},
            source=source,
        )
        if self._policy:
            self._policy.rules = [r for r in self._policy.rules if r.pattern != key]
            self._policy.add_rule(rule)
            self._save()
        return rule

    def set_verbosity(
        self,
        level: Verbosity,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set response verbosity level."""
        return self._set_field("verbosity", level.value, source)

    def set_plan_format(
        self,
        fmt: PlanFormat,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set plan presentation format."""
        return self._set_field("plan_format", fmt.value, source)

    def set_confirm_threshold(
        self,
        threshold: ConfirmThreshold,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set when to ask for user confirmation."""
        return self._set_field("confirm_threshold", threshold.value, source)

    def set_show_commands(
        self,
        show: bool,
        source: PreferenceSource = PreferenceSource.EXPLICIT,
    ) -> PreferenceRule:
        """Set whether to show executed commands in responses."""
        return self._set_field("show_commands", show, source)

    def get_field(self, key: str, default: Any = None) -> Any:
        """Retrieve a preference value by key."""
        if self._policy is None or not self._policy.enabled:
            return default
        for rule in self._policy.get_enabled_rules():
            if rule.pattern == key and rule.action == "set_value":
                return rule.action_args.get("value", default)
        return default

    def get_system_prompt_append(self) -> str:
        """Generate prompt text from current style preferences.

        Returns a string suitable for appending to the system prompt.
        """
        if self._policy is None or not self._policy.enabled:
            return ""

        parts: list[str] = []
        verbosity = self.get_field("verbosity")
        plan_format = self.get_field("plan_format")
        confirm_threshold = self.get_field("confirm_threshold")
        show_commands = self.get_field("show_commands")

        if verbosity == Verbosity.TERSE.value:
            parts.append("Be terse. Use minimal words.")
        elif verbosity == Verbosity.VERBOSE.value:
            parts.append("Be verbose. Explain your reasoning in detail.")

        if plan_format == PlanFormat.BULLETS.value:
            parts.append("Present plans as bullet lists.")
        elif plan_format == PlanFormat.NUMBERED.value:
            parts.append("Present plans as numbered steps.")
        elif plan_format == PlanFormat.DAG.value:
            parts.append("Present plans as a directed acyclic graph (DAG).")

        if confirm_threshold == ConfirmThreshold.NEVER.value:
            parts.append("Do not ask for user confirmation.")
        elif confirm_threshold == ConfirmThreshold.DESTRUCTIVE.value:
            parts.append("Ask for confirmation only before destructive actions.")
        elif confirm_threshold == ConfirmThreshold.ALWAYS.value:
            parts.append("Always ask for user confirmation before acting.")

        if show_commands is True:
            parts.append("Show executed commands in responses.")
        elif show_commands is False:
            parts.append("Do not show executed commands in responses.")

        return "\n".join(parts)
