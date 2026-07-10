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
                    pattern="git",
                    action="append_args",
                    action_args={"args": ["--stat"]},
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

            policy = PreferencePolicy(domain="tools")
            rule = PreferenceRule(
                pattern="git", action="merge_args", action_args={"args": {"flag": "-v"}}
            )
            policy.add_rule(rule)
            reg.save_policy(policy)

            # Batch hits in memory
            reg.batch_hit("tools", rule.rule_id)
            reg.batch_hit("tools", rule.rule_id)

            # Verify not persisted yet
            loaded_before = reg.load_policy("tools")
            assert loaded_before.rules[0].hit_count == 0

            # Flush
            reg.flush_hits()

            # Verify persisted
            loaded_after = reg.load_policy("tools")
            assert loaded_after.rules[0].hit_count == 2
            assert loaded_after.rules[0].last_used_at is not None

    def test_prune_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))

            from vibe.preferences.models import PreferenceSource

            policy = PreferencePolicy(domain="tools")
            # Explicit rule — should NOT be pruned
            policy.add_rule(
                PreferenceRule(pattern="git", action="merge_args", source=PreferenceSource.EXPLICIT)
            )
            # Old inferred rule — should be pruned
            old_rule = PreferenceRule(
                pattern="old",
                action="merge_args",
                source=PreferenceSource.INFERRED,
                last_used_at="2020-01-01T00:00:00+00:00",
                hit_count=5,
            )
            policy.add_rule(old_rule)
            reg.save_policy(policy)

            removed = reg.prune_stale(days=30)
            assert removed == 1

            loaded = reg.load_policy("tools")
            assert len(loaded.rules) == 1
            assert loaded.rules[0].pattern == "git"

    def test_forward_compatibility_extra_fields(self):
        """Old JSON with unknown fields should load without error."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))

            # Manually insert JSON with extra field
            import json
            import sqlite3

            policy_data = {
                "domain": "test",
                "rules": [
                    {
                        "rule_id": "r1",
                        "pattern": "test",
                        "action": "merge_args",
                        "action_args": {},
                        "confidence": 1.0,
                        "source": "explicit",
                        "created_at": "2024-01-01T00:00:00+00:00",
                        "updated_at": "2024-01-01T00:00:00+00:00",
                        "hit_count": 0,
                        "enabled": True,
                        "future_field": "should_be_ignored",
                    }
                ],
                "enabled": True,
                "unknown_policy_field": "also_ignored",
            }

            with sqlite3.connect(str(db)) as conn:
                conn.execute(
                    "INSERT INTO preference_policies (domain, policy_json, enabled) "
                    "VALUES (?, ?, 1)",
                    ("test", json.dumps(policy_data)),
                )

            loaded = reg.load_policy("test")
            assert loaded is not None
            assert loaded.rules[0].pattern == "test"
