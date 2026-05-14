import tempfile
from pathlib import Path

from vibe.preferences.registry import PreferenceRegistry
from vibe.preferences.tool_prefs import ToolPreferenceRegistry


class TestToolPreferenceRegistry:
    def test_set_and_apply_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            reg.set_default_args("git_diff", {"flags": ["--stat"]})
            result = reg.apply("git_diff", {"file": "README.md"})

            assert result["file"] == "README.md"
            assert result["flags"] == ["--stat"]

    def test_user_args_take_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            reg.set_default_args("pytest", {"flags": ["-x"]})
            result = reg.apply("pytest", {"flags": ["-v"]})

            # User-provided -v should not be overwritten
            assert result["flags"] == ["-v"]

    def test_glob_pattern_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            reg.set_default_args("git_*", {"cwd": "."})
            result = reg.apply("git_status", {})
            assert result["cwd"] == "."

    def test_remove_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            reg.set_default_args("test_tool", {"arg": "val"})
            assert reg.remove_default_args("test_tool")
            result = reg.apply("test_tool", {})
            assert "arg" not in result

    def test_hit_count_tracking(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            reg.set_default_args("counter_tool", {"count": 0})
            reg.apply("counter_tool", {})
            reg.apply("counter_tool", {})

            # Hit counts are batched in memory — flush to persist
            reg._registry.flush_hits()

            # Reload to verify persistence
            reg2 = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            rules = reg2.list_preferences()
            assert rules[0].hit_count == 2

    def test_disabled_policy_returns_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            reg.set_default_args("git_diff", {"flags": ["--stat"]})
            reg._policy.enabled = False
            reg._save()

            result = reg.apply("git_diff", {"file": "README.md"})
            assert "flags" not in result
            assert result["file"] == "README.md"

    def test_no_policy_returns_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))

            result = reg.apply("unknown_tool", {"arg": "val"})
            assert result == {"arg": "val"}
