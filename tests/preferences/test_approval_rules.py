import tempfile
from pathlib import Path

from vibe.preferences.approval_rules import ApprovalPolicyDB
from vibe.preferences.registry import PreferenceRegistry


class TestApprovalPolicyDB:
    def test_allow_rule_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("git_diff", "allow")
            decision = policy.check("git_diff", {"file": "README.md"})

            assert decision.action == "allow"
            assert "git_diff" in decision.reason

    def test_deny_rule_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("rm_rf", "deny")
            decision = policy.check("rm_rf", {"path": "/"})

            assert decision.action == "deny"
            assert "deny" in decision.reason.lower()

    def test_no_match_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            # No rules added
            decision = policy.check("unknown_tool", {"arg": "val"})

            assert decision.action == "ask"
            assert decision.rule_id is None

    def test_learn_from_allow_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            rule = policy.learn_from_decision("git_status", {"cwd": "."}, user_decision="allow")

            assert rule is not None
            assert rule.action == "allow"
            assert rule.pattern == "git_status"
            assert rule.source.value == "inferred"

            # Verify the learned rule is persisted and matches
            decision = policy.check("git_status", {"cwd": "."})
            assert decision.action == "allow"

    def test_path_pattern_exact_vs_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            # Exact path match
            exact_file = Path(tmp) / "secret.txt"
            exact_file.write_text("secret")
            policy.add_rule("file_read", "allow", path_pattern=str(exact_file.resolve()))

            decision = policy.check("file_read", {"path": str(exact_file)})
            assert decision.action == "allow"

            # Glob path match
            policy.add_rule("file_write", "deny", path_pattern="*/protected/*")
            protected_dir = Path(tmp) / "protected"
            protected_dir.mkdir()
            protected_file = protected_dir / "data.txt"
            protected_file.write_text("data")

            decision = policy.check("file_write", {"path": str(protected_file)})
            assert decision.action == "deny"

            # Non-matching path should not match the deny rule
            other_file = Path(tmp) / "safe.txt"
            other_file.write_text("safe")
            decision = policy.check("file_write", {"path": str(other_file)})
            assert decision.action == "ask"

    def test_deny_evaluated_before_allow(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("file_*", "allow")
            policy.add_rule("file_delete", "deny")

            decision = policy.check("file_delete", {"path": "/tmp/foo"})
            assert decision.action == "deny"

    def test_path_traversal_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            target_file = Path(tmp) / "secret.txt"
            target_file.write_text("secret")
            policy.add_rule("file_read", "allow", path_pattern=str(target_file.resolve()))

            # Attempt path traversal via symlink or relative path
            traversal_path = Path(tmp) / ".." / Path(tmp).name / "secret.txt"
            decision = policy.check("file_read", {"path": str(traversal_path)})
            assert decision.action == "allow"
            # Path.resolve() normalizes the traversal, so it should match

    def test_arg_constraints_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule(
                "git_checkout",
                "allow",
                arg_constraints={"branch": "main"},
            )

            decision = policy.check("git_checkout", {"branch": "main"})
            assert decision.action == "allow"

            decision = policy.check("git_checkout", {"branch": "feature"})
            assert decision.action == "ask"

    def test_glob_tool_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "approval.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))

            policy.add_rule("git_*", "allow")

            decision = policy.check("git_status", {})
            assert decision.action == "allow"

            decision = policy.check("git_log", {"max_count": 10})
            assert decision.action == "allow"

            decision = policy.check("npm_install", {})
            assert decision.action == "ask"
