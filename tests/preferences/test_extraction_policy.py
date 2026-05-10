import tempfile
from pathlib import Path

from vibe.preferences.extraction_policy import ExtractionPolicy
from vibe.preferences.registry import PreferenceRegistry


class TestExtractionPolicy:
    def test_skip_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_skip_pattern("ignore this")
            assert policy.should_skip("Please ignore this request")
            assert not policy.should_skip("Please process this request")

    def test_skip_pattern_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_skip_pattern("IGNORE")
            assert policy.should_skip("please ignore me")
            assert policy.should_skip("PLEASE IGNORE ME")

    def test_auto_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_auto_tag("python", "programming")
            policy.add_auto_tag("javascript", "programming")
            policy.add_auto_tag("cooking", "lifestyle")

            tags = policy.get_tags_for_content("I love python and javascript")
            assert "programming" in tags
            assert "lifestyle" not in tags

            tags = policy.get_tags_for_content("Let's talk about cooking")
            assert "lifestyle" in tags

    def test_auto_tag_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_auto_tag("Docker", "devops")
            tags = policy.get_tags_for_content("We use docker containers")
            assert "devops" in tags

    def test_merge_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            # Default threshold
            assert policy.get_merge_threshold() == 0.8

            policy.set_merge_threshold(0.95)
            assert policy.get_merge_threshold() == 0.95

            # Reload to verify persistence
            policy2 = ExtractionPolicy(PreferenceRegistry(str(db)))
            assert policy2.get_merge_threshold() == 0.95

    def test_should_skip_returns_false_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ExtractionPolicy(PreferenceRegistry(str(db)))

            policy.add_skip_pattern("skip")
            assert policy.should_skip("skip this")

            policy._policy.enabled = False  # type: ignore[union-attr]
            assert not policy.should_skip("skip this")
