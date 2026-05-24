"""Provider preference matrix — learned model selection rules."""

from __future__ import annotations

from dataclasses import dataclass

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


@dataclass
class ProviderChoice:
    """A provider/model choice with confidence."""

    provider: str
    model: str
    confidence: float  # 0.0-1.0
    reason: str


class ProviderPreferenceMatrix:
    """Learned model selection preferences.

    Mined from user corrections like "use Gemini for code review"
    or "always use Kimi for coding tasks".
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

    def record_preference(
        self,
        task_pattern: str,
        provider: str,
        model: str,
        confidence: float = 0.8,
    ) -> PreferenceRule:
        """Record a provider preference for a task pattern.

        Args:
            task_pattern: Task description pattern (e.g., "code review", "planning")
            provider: Provider name
            model: Model name
            confidence: Confidence level (0.0-1.0)
        """
        rule = PreferenceRule(
            pattern=task_pattern,
            action="prefer",
            action_args={
                "provider": provider,
                "model": model,
                "confidence": confidence,
            },
            source=PreferenceSource.INFERRED,
        )
        if self._policy:
            # Remove duplicates
            self._policy.rules = [
                r
                for r in self._policy.rules
                if not (
                    r.pattern == task_pattern
                    and r.action_args.get("provider") == provider
                )
            ]
            self._policy.add_rule(rule)
            self._save()
        return rule

    def get_preference(self, task: str, min_confidence: float = 0.6) -> ProviderChoice | None:
        """Get provider preference for a task.

        Returns the highest-confidence matching rule above min_confidence.
        """
        if self._policy is None or not self._policy.enabled:
            return None

        best: PreferenceRule | None = None
        best_confidence = 0.0

        for rule in self._policy.get_enabled_rules():
            if rule.pattern.lower() not in task.lower():
                continue

            confidence = rule.action_args.get("confidence", 0.0)
            if confidence >= min_confidence and confidence > best_confidence:
                best = rule
                best_confidence = confidence

        if best:
            # Batch hit count
            self._registry.batch_hit(self.DOMAIN, best.rule_id)
            return ProviderChoice(
                provider=best.action_args["provider"],
                model=best.action_args["model"],
                confidence=best_confidence,
                reason=f"matched rule {best.rule_id}: {best.pattern}",
            )

        return None

    def fallback_chain(self, task: str) -> list[ProviderChoice]:
        """Get ordered fallback chain for a task.

        Returns all matching rules sorted by confidence descending.
        """
        if self._policy is None or not self._policy.enabled:
            return []

        matches = []
        for rule in self._policy.get_enabled_rules():
            if rule.pattern.lower() not in task.lower():
                continue

            confidence = rule.action_args.get("confidence", 0.0)
            matches.append(
                ProviderChoice(
                    provider=rule.action_args["provider"],
                    model=rule.action_args["model"],
                    confidence=confidence,
                    reason=f"matched rule {rule.rule_id}: {rule.pattern}",
                )
            )

        matches.sort(key=lambda x: x.confidence, reverse=True)
        return matches
