import tempfile
from pathlib import Path

from vibe.preferences.compaction_policy import CompactionConfig, CompactionPolicy
from vibe.preferences.registry import PreferenceRegistry


class TestCompactionPolicy:
    def test_set_and_get_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            config = CompactionConfig(max_tokens=4000, preserve_recent_n=2)
            policy.set_config(config)

            loaded = policy.get_config()
            assert loaded.max_tokens == 4000
            assert loaded.preserve_recent_n == 2
            assert loaded.preserve_summary is True  # default

    def test_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            config = policy.get_config()
            assert config.max_tokens == 8000
            assert config.preserve_recent_n == 4

    def test_tool_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_tool_priority("read_file", "keep")
            assert policy.get_tool_priority("read_file") == "keep"
            assert policy.get_tool_priority("bash") is None

    def test_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_config(CompactionConfig(max_tokens=1000))
            policy.set_config(CompactionConfig(max_tokens=2000))

            assert policy.get_config().max_tokens == 2000
