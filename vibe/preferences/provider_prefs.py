"""Provider preference registry — model selection and fallback chains per task type."""

from __future__ import annotations

import logging
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class ProviderPreferenceMatrix:
    """Registry for provider/model preferences.

    Tracks which model the user prefers for each task type and builds
    fallback chains based on observed choices.
    """

    DOMAIN = "provider"

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

    def _get_field(self, key: str, default: Any) -> Any:
        """Read a domain-level setting from policy extra data."""
        if self._policy is None:
            return default
        return self._policy.model_dump().get(key, default)

    def _set_field(self, key: str, value: Any) -> None:
        """Write a domain-level setting into policy extra data."""
        if self._policy is None:
            return
        # Pydantic v2: mutate via reconstruction to keep validation
        data = self._policy.model_dump()
        data[key] = value
        self._policy = PreferencePolicy(**data)
        self._save()

    def record_choice(
        self,
        task_type: str,
        chosen_model: str,
        available_models: list[str],
        context: dict[str, Any] | None = None,
    ) -> PreferenceRule:
        """Record that a model was chosen for a task type.

        Args:
            task_type: E.g. "coding", "summarization", "planning"
            chosen_model: The model that was selected
            available_models: Full list of models that were available
            context: Optional extra context about the choice
        """
        # Find existing rule for this task_type
        existing = None
        if self._policy:
            for rule in self._policy.rules:
                if rule.pattern == task_type and rule.action == "prefer_model":
                    existing = rule
                    break

        if existing is not None:
            # Update choice count and preferred model
            choice_count = existing.action_args.get("choice_count", 0) + 1
            action_args: dict[str, Any] = {
                "chosen_model": chosen_model,
                "available_models": available_models,
                "choice_count": choice_count,
            }
            if context is not None:
                action_args["context"] = context

            existing.action_args = action_args
            existing.updated_at = (
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            )
            self._save()
            return existing

        # Create new rule
        rule = PreferenceRule(
            pattern=task_type,
            action="prefer_model",
            action_args={
                "chosen_model": chosen_model,
                "available_models": available_models,
                "choice_count": 1,
            },
            source=PreferenceSource.INFERRED,
        )
        if context is not None:
            rule.action_args["context"] = context

        if self._policy:
            self._policy.add_rule(rule)
            self._save()
        return rule

    def get_preferred_model(
        self,
        task_type: str,
        default_model: str,
        min_confidence: int = 2,
    ) -> str:
        """Get the preferred model for a task type if confidence is high enough.

        Args:
            task_type: The task type to look up
            default_model: Fallback model if no preference or below threshold
            min_confidence: Minimum choice_count required to trust the preference

        Returns:
            The preferred model name or default_model
        """
        if self._policy is None or not self._policy.enabled:
            return default_model

        for rule in self._policy.get_enabled_rules():
            if rule.pattern == task_type and rule.action == "prefer_model":
                choice_count = rule.action_args.get("choice_count", 0)
                if choice_count >= min_confidence:
                    self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                    return rule.action_args.get("chosen_model", default_model)
                break

        return default_model

    def get_fallback_chain(self, task_type: str) -> list[str]:
        """Return the ordered list of available models for a task type.

        The chosen model is moved to the front; remaining models follow
        in their original order.
        """
        if self._policy is None or not self._policy.enabled:
            return []

        for rule in self._policy.get_enabled_rules():
            if rule.pattern == task_type and rule.action == "prefer_model":
                chosen = rule.action_args.get("chosen_model")
                available = list(rule.action_args.get("available_models", []))
                if not available:
                    return [chosen] if chosen else []

                # Build chain: chosen first, then others in original order
                chain: list[str] = []
                if chosen and chosen in available:
                    chain.append(chosen)
                for model in available:
                    if model != chosen:
                        chain.append(model)
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                return chain

        return []
