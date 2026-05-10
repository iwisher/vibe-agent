import tempfile
from pathlib import Path

from vibe.preferences.registry import PreferenceRegistry
from vibe.preferences.style_policy import (
    ConfirmThreshold,
    PlanFormat,
    ResponseStylePolicy,
    Verbosity,
)


class TestResponseStylePolicy:
    def test_set_and_get_verbosity(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ResponseStylePolicy(PreferenceRegistry(str(db)))

            policy.set_verbosity(Verbosity.TERSE)
            assert policy.get_field("verbosity") == "terse"

            policy.set_verbosity(Verbosity.VERBOSE)
            assert policy.get_field("verbosity") == "verbose"

    def test_system_prompt_combines_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ResponseStylePolicy(PreferenceRegistry(str(db)))

            policy.set_verbosity(Verbosity.VERBOSE)
            policy.set_plan_format(PlanFormat.NUMBERED)
            policy.set_confirm_threshold(ConfirmThreshold.DESTRUCTIVE)
            policy.set_show_commands(True)

            prompt = policy.get_system_prompt_append()
            assert "verbose" in prompt
            assert "numbered steps" in prompt
            assert "destructive" in prompt
            assert "Show executed commands" in prompt

    def test_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ResponseStylePolicy(PreferenceRegistry(str(db)))

            policy.set_verbosity(Verbosity.TERSE)
            policy.set_verbosity(Verbosity.NORMAL)

            rules = policy._policy.rules if policy._policy else []
            assert len(rules) == 1
            assert policy.get_field("verbosity") == "normal"

    def test_empty_policy_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ResponseStylePolicy(PreferenceRegistry(str(db)))

            assert policy.get_system_prompt_append() == ""
            assert policy.get_field("verbosity") is None
            assert policy.get_field("nonexistent", "fallback") == "fallback"
