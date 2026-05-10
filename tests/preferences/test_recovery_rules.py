import tempfile
from pathlib import Path

from vibe.preferences.recovery_rules import RecoveryRuleDB
from vibe.preferences.registry import PreferenceRegistry


class TestRecoveryRuleDB:
    def test_add_and_find_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = RecoveryRuleDB(PreferenceRegistry(str(db)))

            reg.add_rule(
                error_pattern="Connection refused",
                recovery_action="retry_with",
                recovery_args={"delay": 1.0},
                tool_name="bash",
                max_attempts=3,
            )

            rule = reg.find_recovery("Connection refused to host", tool_name="bash")
            assert rule is not None
            assert rule.action == "retry_with"
            assert rule.action_args["recovery_args"] == {"delay": 1.0}
            assert rule.action_args["tool_name"] == "bash"
            assert rule.action_args["max_attempts"] == 3

    def test_attempt_limits_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = RecoveryRuleDB(PreferenceRegistry(str(db)))

            reg.add_rule(
                error_pattern="*timeout*",
                recovery_action="fallback_to",
                recovery_args={"model": "backup"},
                max_attempts=2,
            )

            session_state: dict = {}

            # First two calls should succeed
            r1 = reg.find_recovery("Read timeout occurred", session_state=session_state)
            assert r1 is not None
            r2 = reg.find_recovery("Write timeout occurred", session_state=session_state)
            assert r2 is not None

            # Third call should exceed max_attempts and return None
            r3 = reg.find_recovery("Another timeout", session_state=session_state)
            assert r3 is None

            # Verify attempt count tracked in session_state
            assert session_state["recovery_attempts"][r1.rule_id] == 2

    def test_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = RecoveryRuleDB(PreferenceRegistry(str(db)))

            reg.add_rule(
                error_pattern="Disk full",
                recovery_action="ask_user",
                recovery_args={},
            )

            rule = reg.find_recovery("Network unreachable")
            assert rule is None

    def test_tool_scoping(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = RecoveryRuleDB(PreferenceRegistry(str(db)))

            reg.add_rule(
                error_pattern="Permission denied",
                recovery_action="ask_user",
                recovery_args={},
                tool_name="file_write",
                max_attempts=1,
            )

            # Same error for a different tool should not match
            no_match = reg.find_recovery("Permission denied", tool_name="bash")
            assert no_match is None

            # Same error for the scoped tool should match
            match = reg.find_recovery("Permission denied", tool_name="file_write")
            assert match is not None
            assert match.action_args["tool_name"] == "file_write"

    def test_remove_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = RecoveryRuleDB(PreferenceRegistry(str(db)))

            reg.add_rule(
                error_pattern="OOM",
                recovery_action="fallback_to",
                recovery_args={"model": "smaller"},
            )
            assert reg.remove_rule("OOM")
            assert reg.find_recovery("OOM error") is None
