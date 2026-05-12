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
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))

            style.set_verbosity(Verbosity.TERSE)
            assert style.get_field("verbosity") == "terse"
            prompt = style.get_system_prompt_append()
            assert "concise" in prompt

    def test_system_prompt_combines_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))

            style.set_verbosity(Verbosity.TERSE)
            style.set_confirm_threshold(ConfirmThreshold.NEVER)
            style.set_show_commands(True)

            prompt = style.get_system_prompt_append()
            assert "concise" in prompt
            assert "Never ask" in prompt
            assert "show the exact command" in prompt

    def test_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))

            style.set_verbosity(Verbosity.TERSE)
            style.set_verbosity(Verbosity.VERBOSE)

            assert style.get_field("verbosity") == "verbose"
            prompt = style.get_system_prompt_append()
            assert "thorough" in prompt
            assert "concise" not in prompt

    def test_empty_policy_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))

            assert style.get_system_prompt_append() == ""
