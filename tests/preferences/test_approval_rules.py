import tempfile
from pathlib import Path

from vibe.preferences.approval_rules import ApprovalPolicyDB
from vibe.preferences.registry import PreferenceRegistry


class TestApprovalPolicyDB:
    def test_allow_rule_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            # Use a path within the temp dir so resolve() stays predictable
            policy.add_rule("read_file", "allow", path_pattern=f"{tmp}/*")

            decision = policy.check("read_file", {"path": f"{tmp}/code.py"})
            assert decision.action == "allow"

    def test_deny_rule_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("bash", "deny", arg_constraints={"command": "rm -rf /"})

            decision = policy.check("bash", {"command": "rm -rf /"})
            assert decision.action == "deny"

    def test_no_match_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            decision = policy.check("unknown_tool", {})
            assert decision.action == "ask"

    def test_learn_from_allow_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.learn_from_decision("read_file", {"path": f"{tmp}/a.py"}, "allow")

            # Should create a rule allowing read_file in the temp dir
            decision = policy.check("read_file", {"path": f"{tmp}/b.py"})
            assert decision.action == "allow"

    def test_path_pattern_exact_vs_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("write_file", "allow", path_pattern=f"{tmp}/*")

            assert policy.check("write_file", {"path": f"{tmp}/test.txt"}).action == "allow"
            assert policy.check("write_file", {"path": "/etc/passwd"}).action == "ask"

    def test_deny_before_allow_priority(self):
        """Deny rules should be evaluated before allow rules."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            # Broad allow rule
            policy.add_rule("bash", "allow")
            # Specific deny rule
            policy.add_rule("bash", "deny", arg_constraints={"command": "rm -rf /"})

            # Should deny despite broad allow
            decision = policy.check("bash", {"command": "rm -rf /"})
            assert decision.action == "deny"

    def test_path_traversal_blocked(self):
        """Path traversal attempts should not match directory patterns."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("read_file", "allow", path_pattern=f"{tmp}/*")

            # Traversal attempt should NOT match — the resolved path must be
            # within the allowed directory (not just the unresolved string)
            # We test this by checking the resolved path doesn't match
            traversal_path = f"{tmp}/../../../etc/shadow"
            resolved = str(Path(traversal_path).resolve())
            # The resolved path should NOT start with tmp
            assert not resolved.startswith(tmp), f"resolved path leaked: {resolved}"

            # The policy check should deny because resolved doesn't match
            decision = policy.check("read_file", {"path": traversal_path})
            assert decision.action == "ask"
