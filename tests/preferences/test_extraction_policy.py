import tempfile
from pathlib import Path

from vibe.preferences.extraction_policy import ExtractionConfig, ExtractionPolicy
from vibe.preferences.registry import PreferenceRegistry


class TestExtractionPolicy:
    def test_set_and_get_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            config = ExtractionConfig(auto_tag=False, min_confidence=0.8)
            policy.set_config(config)

            loaded = policy.get_config()
            assert loaded.auto_tag is False
            assert loaded.min_confidence == 0.8

    def test_skip_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_skip_pattern("password")
            assert policy.should_skip("my password is secret")
            assert not policy.should_skip("normal message")

    def test_skip_pattern_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_skip_pattern("SECRET")
            assert policy.should_skip("my secret info")

    def test_auto_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_auto_tag("finance", "finance")
            tags = policy.get_tags("discussing finance topics")
            assert "finance" in tags

    def test_auto_tag_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_auto_tag("Finance", "finance")
            tags = policy.get_tags("discussing finance topics")
            assert "finance" in tags

    def test_merge_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.set_config(ExtractionConfig(merge_threshold=0.9))
            assert policy.get_config().merge_threshold == 0.9

    def test_should_skip_returns_false_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_skip_pattern("secret")
            # Disable the policy
            if policy._policy:
                policy._policy.enabled = False

            assert not policy.should_skip("my secret info")
