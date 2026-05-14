"""Preference Layer for vibe-agent.

Converts user feedback into persistent, testable, code-based heuristics.
"""

from vibe.preferences.registry import PreferenceRegistry
from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource

__all__ = ["PreferenceRegistry", "PreferencePolicy", "PreferenceRule", "PreferenceSource"]
