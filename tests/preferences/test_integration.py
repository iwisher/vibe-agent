"""Integration tests for the preference layer."""

import tempfile
from pathlib import Path

from vibe.preferences.approval_rules import ApprovalPolicyDB
from vibe.preferences.compaction_policy import CompactionConfig, CompactionPolicy
from vibe.preferences.extraction_policy import ExtractionConfig, ExtractionPolicy
from vibe.preferences.models import PreferencePolicy
from vibe.preferences.provider_prefs import ProviderPreferenceMatrix
from vibe.preferences.recovery_rules import RecoveryRuleDB
from vibe.preferences.registry import PreferenceRegistry
from vibe.preferences.style_policy import ConfirmThreshold, ResponseStylePolicy, Verbosity
from vibe.preferences.tool_prefs import ToolPreferenceRegistry


class TestToolPreferenceIntegration:
    def test_tool_executor_applies_preferences(self):
        """Tool preferences are applied when a tool is called."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            prefs = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            prefs.set_default_args("read_file", {"limit": 50})
            result = prefs.apply("read_file", {"path": "/tmp/test.py"})

            assert result["path"] == "/tmp/test.py"
            assert result["limit"] == 50

    def test_multiple_domains_isolated(self):
        """Different preference domains don't interfere."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            registry = PreferenceRegistry(str(db))

            tools = ToolPreferenceRegistry(registry)
            style = ResponseStylePolicy(registry)
            approval = ApprovalPolicyDB(registry)

            tools.set_default_args("read_file", {"limit": 50})
            style.set_verbosity(Verbosity.TERSE)
            approval.add_rule("bash", "deny")

            # Each domain should only see its own rules
            assert len(tools._policy.rules) == 1
            assert len(style._policy.rules) == 1
            assert len(approval._policy.rules) == 1


class TestCrossPreferenceInteractions:
    def test_style_and_approval_combined(self):
        """Style policy and approval policy work together."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            registry = PreferenceRegistry(str(db))

            style = ResponseStylePolicy(registry)
            approval = ApprovalPolicyDB(registry)

            style.set_confirm_threshold(ConfirmThreshold.DESTRUCTIVE)
            approval.add_rule("write_file", "allow", path_pattern=f"{tmp}/*")

            # Style says "only confirm destructive" — approval says "allow write_file"
            # The combined behavior: write_file in tmp is allowed without confirmation
            decision = approval.check("write_file", {"path": f"{tmp}/test.txt"})
            assert decision.action == "allow"

    def test_provider_and_tool_prefs(self):
        """Provider preferences can influence tool defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            registry = PreferenceRegistry(str(db))

            providers = ProviderPreferenceMatrix(registry)
            tools = ToolPreferenceRegistry(registry)

            providers.record_preference("coding", "kimi", "kimi-k2", confidence=0.9)
            tools.set_default_args("read_file", {"limit": 100})

            choice = providers.get_preference("coding task")
            assert choice.provider == "kimi"

            args = tools.apply("read_file", {"path": "/tmp/test.py"})
            assert args["limit"] == 100


class TestPreferenceRegistryIntegration:
    def test_all_domains_persist_together(self):
        """All preference domains share one database."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            registry = PreferenceRegistry(str(db))

            # Populate multiple domains
            ToolPreferenceRegistry(registry).set_default_args("read_file", {"limit": 50})
            ResponseStylePolicy(registry).set_verbosity(Verbosity.VERBOSE)
            ApprovalPolicyDB(registry).add_rule("bash", "deny")
            RecoveryRuleDB(registry).add_rule("write_file", "error", "bash", {})
            CompactionPolicy(registry).set_config(CompactionConfig(max_tokens=4000))
            ProviderPreferenceMatrix(registry).record_preference("test", "p", "m")
            ExtractionPolicy(registry).set_config(ExtractionConfig(auto_tag=False))

            # Verify all domains are listed
            domains = registry.list_domains()
            assert "tools" in domains
            assert "style" in domains
            assert "approval" in domains
            assert "recovery" in domains
            assert "compaction" in domains
            assert "provider" in domains
            assert "extraction" in domains

    def test_prune_stale_across_domains(self):
        """Pruning stale rules works across all domains."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            registry = PreferenceRegistry(str(db))

            # Add an INFERRED rule with old last_used_at (only inferred rules are pruned)
            from vibe.preferences.models import PreferenceRule, PreferenceSource

            policy = registry.load_policy("tools") or PreferencePolicy(domain="tools")
            policy.add_rule(
                PreferenceRule(
                    pattern="old_tool",
                    action="merge_args",
                    action_args={"args": {"limit": 50}},
                    source=PreferenceSource.INFERRED,
                    last_used_at="2026-01-01T00:00:00+00:00",
                )
            )
            registry.save_policy(policy)

            # Prune rules older than 30 days
            removed = registry.prune_stale(days=30)
            assert removed == 1

            # Reload and verify
            reloaded = registry.load_policy("tools")
            assert reloaded is not None
            assert len(reloaded.rules) == 0
