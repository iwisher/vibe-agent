import tempfile
from pathlib import Path

from vibe.preferences.provider_prefs import ProviderPreferenceMatrix
from vibe.preferences.registry import PreferenceRegistry


class TestProviderPreferenceMatrix:
    def test_record_and_retrieve_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            matrix.record_preference("code review", "gemini", "gemini-1.5-pro", confidence=0.9)

            choice = matrix.get_preference("I need a code review")
            assert choice is not None
            assert choice.provider == "gemini"
            assert choice.model == "gemini-1.5-pro"
            assert choice.confidence == 0.9

    def test_confidence_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            matrix.record_preference("planning", "kimi", "kimi-k2", confidence=0.5)

            # Below default min_confidence of 0.6
            assert matrix.get_preference("planning task") is None
            # Lower threshold should match
            choice = matrix.get_preference("planning task", min_confidence=0.4)
            assert choice is not None

    def test_fallback_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            matrix.record_preference("coding", "kimi", "kimi-k2", confidence=0.9)
            matrix.record_preference("coding", "gemini", "gemini-1.5-pro", confidence=0.7)

            chain = matrix.fallback_chain("coding task")
            assert len(chain) == 2
            assert chain[0].provider == "kimi"  # higher confidence first
            assert chain[1].provider == "gemini"

    def test_fallback_chain_empty_when_no_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            assert matrix.fallback_chain("unknown task") == []

    def test_choice_count_tracked_in_action_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            matrix = ProviderPreferenceMatrix(PreferenceRegistry(str(db)))

            rule = matrix.record_preference("test", "provider", "model", confidence=0.8)
            assert rule.action_args["confidence"] == 0.8
            assert rule.action_args["provider"] == "provider"
            assert rule.action_args["model"] == "model"
