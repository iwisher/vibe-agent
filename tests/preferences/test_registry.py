import tempfile
from pathlib import Path

from vibe.preferences.models import PreferencePolicy, PreferenceRule
from vibe.preferences.registry import PreferenceRegistry


class TestPreferenceRegistry:
    def test_save_and_load_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))

            policy = PreferencePolicy(domain="test")
            policy.add_rule(
                PreferenceRule(
                    pattern="git", action="append_args", action_args={"args": ["--stat"]}
                )
            )
            reg.save_policy(policy)

            loaded = reg.load_policy("test")
            assert loaded is not None
            assert loaded.domain == "test"
            assert len(loaded.rules) == 1
            assert loaded.rules[0].pattern == "git"

    def test_list_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))
            reg.save_policy(PreferencePolicy(domain="tools"))
            reg.save_policy(PreferencePolicy(domain="style"))
            assert sorted(reg.list_domains()) == ["style", "tools"]

    def test_delete_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))
            reg.save_policy(PreferencePolicy(domain="tools"))
            assert reg.delete_policy("tools")
            assert reg.load_policy("tools") is None

    def test_batch_hit_and_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))

            policy = PreferencePolicy(domain="test")
            rule = PreferenceRule(pattern="bash", action="merge_args", action_args={"args": {}})
            policy.add_rule(rule)
            reg.save_policy(policy)

            reg.batch_hit("test", rule.rule_id)
            reg.batch_hit("test", rule.rule_id)
            reg.flush_hits()

            loaded = reg.load_policy("test")
            assert loaded.rules[0].hit_count == 2
            assert loaded.rules[0].last_used_at is not None
