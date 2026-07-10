import tempfile
from pathlib import Path

from vibe.preferences.recovery_rules import RecoveryRuleDB
from vibe.preferences.registry import PreferenceRegistry


class TestRecoveryRuleDB:
    def test_add_and_find_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            recovery = RecoveryRuleDB(PreferenceRegistry(str(db)))

            recovery.add_rule(
                "write_file",
                "permission denied",
                "bash",
                {"command": "chmod +w {{path}}"},
                max_attempts=2,
            )

            session_state = {}
            action = recovery.find_recovery(
                "write_file", "Error: Permission denied on /tmp/file", session_state
            )
            assert action is not None
            assert action.recovery_tool == "bash"
            assert action.max_attempts == 2

    def test_attempt_limits_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            recovery = RecoveryRuleDB(PreferenceRegistry(str(db)))

            recovery.add_rule(
                "write_file",
                "permission denied",
                "bash",
                {"command": "chmod +w {{path}}"},
                max_attempts=2,
            )

            session_state = {}
            # First two attempts succeed
            assert (
                recovery.find_recovery("write_file", "permission denied", session_state) is not None
            )
            assert (
                recovery.find_recovery("write_file", "permission denied", session_state) is not None
            )
            # Third attempt fails (max_attempts=2)
            assert recovery.find_recovery("write_file", "permission denied", session_state) is None

    def test_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            recovery = RecoveryRuleDB(PreferenceRegistry(str(db)))

            session_state = {}
            assert recovery.find_recovery("unknown_tool", "some error", session_state) is None

    def test_tool_scoping(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            recovery = RecoveryRuleDB(PreferenceRegistry(str(db)))

            recovery.add_rule("bash", "not found", "write_file", {"content": ""})

            session_state = {}
            # Different tool with same error should not match
            assert recovery.find_recovery("read_file", "not found", session_state) is None

    def test_remove_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            recovery = RecoveryRuleDB(PreferenceRegistry(str(db)))

            recovery.add_rule("bash", "not found", "write_file", {})
            assert recovery.remove_rule("bash", "not found") is True

            session_state = {}
            assert recovery.find_recovery("bash", "not found", session_state) is None
