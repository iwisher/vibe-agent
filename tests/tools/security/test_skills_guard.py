"""Tests for SkillsGuard."""


import pytest

from vibe.tools.security.skills_guard import (
    SkillRestriction,
    SkillsGuard,
)


class TestSkillsGuard:
    """Test SkillsGuard restrictions."""

    def test_deny_all_mode(self):
        """DENY_ALL should block everything."""
        guard = SkillsGuard(restriction_level=SkillRestriction.DENY_ALL)

        result = guard.check_skill_code("print('hello')")
        assert not result.allowed
        assert "DENY_ALL" in result.reason

    def test_read_only_blocks_writes(self):
        """READ_ONLY should block write operations."""
        guard = SkillsGuard(restriction_level=SkillRestriction.READ_ONLY)

        result = guard.check_skill_code("with open('file.txt', 'w') as f: f.write('x')")
        assert not result.allowed
        assert "write" in result.reason.lower() or "read-only" in result.reason.lower()

    def test_read_only_allows_reads(self):
        """READ_ONLY should allow read operations."""
        guard = SkillsGuard(restriction_level=SkillRestriction.READ_ONLY)

        result = guard.check_skill_code("with open('file.txt', 'r') as f: content = f.read()")
        assert result.allowed

    def test_sandboxed_allows_safe_code(self):
        """SANDBOXED should allow safe code."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        result = guard.check_skill_code("print('hello world')")
        assert result.allowed
        assert "passed security checks" in result.reason

    def test_blocks_dangerous_patterns(self):
        """Should block dangerous shell commands."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        dangerous_code = [
            "os.system('rm -rf /')",
            "subprocess.call(['sudo', 'ls'])",
            "eval('__import__(\"os\").system(\"ls\")')",
            "exec('print(1)')",
        ]

        for code in dangerous_code:
            result = guard.check_skill_code(code)
            assert not result.allowed, f"Should block: {code}"
            assert "Dangerous" in result.reason

    def test_file_access_senstive_paths(self):
        """Should block access to sensitive paths."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        result = guard.check_file_access("~/.ssh/id_rsa")
        assert not result.allowed
        assert "sensitive path" in result.reason.lower()

    def test_file_access_allowed_paths(self):
        """Should allow access to normal paths."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        result = guard.check_file_access("/tmp/test.txt")
        assert result.allowed

    def test_workspace_restriction(self, tmp_path):
        """Should enforce workspace boundaries."""
        guard = SkillsGuard(
            restriction_level=SkillRestriction.SANDBOXED,
            allowed_workspace=tmp_path,
        )

        # Inside workspace
        result = guard.check_file_access(str(tmp_path / "test.txt"))
        assert result.allowed

        # Outside workspace (use a path that's not also sensitive)
        result = guard.check_file_access("/tmp/outside_workspace.txt")
        assert not result.allowed
        assert "outside allowed workspace" in result.reason

    def test_file_extension_check(self, tmp_path):
        """Should check file extensions for existing files."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        # Create an allowed file
        allowed_file = tmp_path / "test.txt"
        allowed_file.write_text("content")
        result = guard.check_file_access(str(allowed_file))
        assert result.allowed

        # Create a disallowed file
        disallowed_file = tmp_path / "test.exe"
        disallowed_file.write_text("binary")
        result = guard.check_file_access(str(disallowed_file))
        assert not result.allowed
        assert "extension" in result.reason.lower()

    def test_file_size_limit(self, tmp_path):
        """Should enforce file size limits."""
        guard = SkillsGuard(
            restriction_level=SkillRestriction.SANDBOXED,
            max_file_size_mb=0.001,  # 1 KB
        )

        # Create a large file
        large_file = tmp_path / "large.txt"
        large_file.write_text("x" * 2000)  # 2 KB

        result = guard.check_file_access(str(large_file), operation="read")
        assert not result.allowed
        assert "size" in result.reason.lower()

    def test_subagent_spawn_sandboxed(self):
        """SANDBOXED should restrict dangerous sub-agent capabilities."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        # Safe capabilities
        result = guard.check_subagent_spawn("safe_agent", ["read_file", "write_file"])
        assert result.allowed

        # Dangerous capabilities
        result = guard.check_subagent_spawn("dangerous_agent", ["terminal", "shell"])
        assert not result.allowed
        assert "terminal" in result.reason

    def test_subagent_spawn_readonly(self):
        """READ_ONLY should allow read-only sub-agents."""
        guard = SkillsGuard(restriction_level=SkillRestriction.READ_ONLY)

        result = guard.check_subagent_spawn("reader", ["read_file"])
        assert result.allowed

    def test_network_access_blocked_in_sandbox(self):
        """SANDBOXED should block network access."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        result = guard.check_network_access("https://example.com")
        assert not result.allowed
        assert "denied" in result.reason.lower()

    def test_network_access_allowed_in_readonly(self):
        """READ_ONLY should allow network access."""
        guard = SkillsGuard(restriction_level=SkillRestriction.READ_ONLY)

        result = guard.check_network_access("https://example.com")
        assert result.allowed

    def test_network_access_blocks_internal_ips(self):
        """Should block access to internal/private IPs."""
        guard = SkillsGuard(restriction_level=SkillRestriction.READ_ONLY)

        internal_urls = [
            "http://localhost:8080",
            "http://127.0.0.1:5000",
            "http://192.168.1.1",
            "http://10.0.0.1",
        ]

        for url in internal_urls:
            result = guard.check_network_access(url)
            assert not result.allowed, f"Should block internal URL: {url}"
            assert "internal" in result.reason.lower()

    def test_wrap_skill_execution_deny_all(self):
        """wrap_skill_execution should raise in DENY_ALL mode."""
        guard = SkillsGuard(restriction_level=SkillRestriction.DENY_ALL)

        with pytest.raises(PermissionError):
            guard.wrap_skill_execution(lambda: print("hello"))

    def test_wrap_skill_execution_allowed(self):
        """wrap_skill_execution should run in allowed modes."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        def test_func():
            return "success"

        result = guard.wrap_skill_execution(test_func)
        assert result == "success"

    def test_none_mode_allows_most_operations(self):
        """NONE restriction should allow most operations but still block extreme dangers."""
        guard = SkillsGuard(restriction_level=SkillRestriction.NONE)

        # Should still block rm -rf (extreme danger)
        result = guard.check_skill_code("os.system('rm -rf /')")
        assert not result.allowed

        # Should allow normal file access
        result = guard.check_file_access("/tmp/test.txt")
        assert result.allowed

        # Should allow network access to external URLs
        result = guard.check_network_access("https://example.com")
        assert result.allowed

    def test_blocks_rm_rf(self):
        """Should specifically block rm -rf."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        result = guard.check_skill_code("import os; os.system('rm -rf /important')")
        assert not result.allowed
        assert "rm -rf" in result.reason or "Dangerous" in result.reason

    def test_blocks_sudo(self):
        """Should block sudo commands."""
        guard = SkillsGuard(restriction_level=SkillRestriction.SANDBOXED)

        result = guard.check_skill_code("os.system('sudo apt-get install x')")
        assert not result.allowed
        assert "sudo" in result.reason or "Dangerous" in result.reason
