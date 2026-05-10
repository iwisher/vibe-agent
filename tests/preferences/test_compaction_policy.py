import tempfile
from pathlib import Path

from vibe.preferences.compaction_policy import CompactionPolicy, CompactionStrategy
from vibe.preferences.registry import PreferenceRegistry


class TestCompactionPolicy:
    def test_set_and_get_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_strategy(CompactionStrategy.LLM_SUMMARIZE)
            assert policy.get_strategy() == CompactionStrategy.LLM_SUMMARIZE

            policy.set_strategy(CompactionStrategy.DROP)
            assert policy.get_strategy() == CompactionStrategy.DROP

    def test_default_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            assert policy.get_strategy() is None
            assert policy.get_drop_priority() is None
            assert policy.get_never_summarize() is None
            assert policy.get_offload_threshold() is None

    def test_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_strategy(CompactionStrategy.TRUNCATE)
            policy.set_strategy(CompactionStrategy.OFFLOAD)

            # Should only have one rule after overwrite
            rules = policy.list_settings()
            assert len(rules) == 1
            assert rules[0].action_args["value"] == CompactionStrategy.OFFLOAD.value
            assert policy.get_strategy() == CompactionStrategy.OFFLOAD

    def test_set_and_get_drop_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_drop_priority(["system", "assistant", "user"])
            assert policy.get_drop_priority() == ["system", "assistant", "user"]

    def test_set_and_get_never_summarize(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_never_summarize(["tool_result", "approval_request"])
            assert policy.get_never_summarize() == ["tool_result", "approval_request"]

    def test_set_and_get_offload_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = CompactionPolicy(PreferenceRegistry(str(db)))

            policy.set_offload_threshold(8000)
            assert policy.get_offload_threshold() == 8000
