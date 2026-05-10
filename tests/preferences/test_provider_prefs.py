import tempfile
from pathlib import Path

from vibe.preferences.provider_prefs import ProviderPreferenceMatrix
from vibe.preferences.registry import PreferenceRegistry


class TestProviderPreferenceMatrix:
    def test_record_and_retrieve_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            matrix.record_choice(
                task_type="coding",
                chosen_model="gpt-4",
                available_models=["gpt-4", "claude-3", "gemini-pro"],
            )
            matrix.record_choice(
                task_type="coding",
                chosen_model="gpt-4",
                available_models=["gpt-4", "claude-3", "gemini-pro"],
            )

            preferred = matrix.get_preferred_model("coding", default_model="claude-3")
            assert preferred == "gpt-4"

    def test_confidence_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            # Single choice — below min_confidence=2
            matrix.record_choice(
                task_type="summarization",
                chosen_model="gemini-pro",
                available_models=["gpt-4", "gemini-pro"],
            )

            # Should fall back to default because choice_count == 1 < 2
            preferred = matrix.get_preferred_model(
                "summarization", default_model="claude-3", min_confidence=2
            )
            assert preferred == "claude-3"

            # Second choice pushes count to 2
            matrix.record_choice(
                task_type="summarization",
                chosen_model="gemini-pro",
                available_models=["gpt-4", "gemini-pro"],
            )
            preferred = matrix.get_preferred_model(
                "summarization", default_model="claude-3", min_confidence=2
            )
            assert preferred == "gemini-pro"

    def test_fallback_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            matrix.record_choice(
                task_type="planning",
                chosen_model="claude-3",
                available_models=["gpt-4", "claude-3", "gemini-pro"],
            )

            chain = matrix.get_fallback_chain("planning")
            assert chain == ["claude-3", "gpt-4", "gemini-pro"]

    def test_fallback_chain_empty_when_no_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            chain = matrix.get_fallback_chain("unknown_task")
            assert chain == []

    def test_choice_count_tracked_in_action_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            matrix.record_choice(
                task_type="coding",
                chosen_model="gpt-4",
                available_models=["gpt-4", "claude-3"],
            )
            matrix.record_choice(
                task_type="coding",
                chosen_model="gpt-4",
                available_models=["gpt-4", "claude-3"],
            )
            matrix.record_choice(
                task_type="coding",
                chosen_model="gpt-4",
                available_models=["gpt-4", "claude-3"],
            )

            rules = matrix._policy.rules if matrix._policy else []
            assert len(rules) == 1
            assert rules[0].action_args["choice_count"] == 3
