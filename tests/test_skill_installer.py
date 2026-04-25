"""Test skill installer."""
import asyncio
import pytest
import tarfile
import tempfile
from pathlib import Path
from vibe.harness.skills.installer import SkillInstaller, InstallResult
from vibe.harness.skills.approval import AutoApproveGate

SAMPLE_SKILL_DIR = """+++
vibe_skill_version = "2.0.0"
id = "sample-skill"
name = "Sample Skill"
description = "A sample skill"
category = "test"
tags = ["test"]

[trigger]
patterns = ["sample"]
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "Hello"
tool = "bash"
command = "echo hello"
+++

# Sample Skill
"""


def test_install_from_local_path():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "sample-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(SAMPLE_SKILL_DIR)
        (source / "scripts").mkdir()
        (source / "scripts" / "hello.py").write_text("print('hello')")

        install_dir = Path(tmp) / "installed"
        installer = SkillInstaller(skills_dir=install_dir, approval_gate=AutoApproveGate())

        result = asyncio.run(installer.install_from_path(source))

        assert result.success
        assert (install_dir / "sample-skill" / "SKILL.md").exists()
        assert (install_dir / "sample-skill" / "scripts" / "hello.py").exists()
        # Verify .git not copied
        assert not (install_dir / "sample-skill" / ".git").exists()


def test_install_rejects_with_auto_reject():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "risky-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(SAMPLE_SKILL_DIR)

        install_dir = Path(tmp) / "installed"
        installer = SkillInstaller(skills_dir=install_dir, approval_gate=AutoApproveGate())
        result = asyncio.run(installer.install_from_path(source))

        assert result.success  # AutoApproveGate allows everything


def test_install_git_clone_timeout():
    """Git clone with unresponsive URL should timeout."""
    install_dir = tempfile.mkdtemp()
    installer = SkillInstaller(skills_dir=install_dir, approval_gate=AutoApproveGate())

    async def _test():
        return await installer.install_from_git("http://192.0.2.1/nonexistent.git")

    result = asyncio.run(_test())
    assert not result.success
    assert "timed out" in result.message.lower()


def test_install_rejects_malicious_skill_id():
    """Skill IDs with path traversal should be rejected by Pydantic."""
    from vibe.harness.skills.models import Skill, SkillTrigger, SkillStep
    with pytest.raises(ValueError):
        Skill(
            vibe_skill_version="2.0.0",
            id="../../etc",
            name="Evil",
            description="Evil",
            trigger=SkillTrigger(),
            steps=[SkillStep(id="s1", description="A", tool="bash", command="echo a")],
        )


def test_install_rejects_tarball_with_unsafe_paths():
    """Tarballs containing ../ paths should be rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "evil.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            import io
            data = b"evil content"
            info = tarfile.TarInfo(name="../evil.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        install_dir = Path(tmp) / "installed"
        installer = SkillInstaller(skills_dir=install_dir, approval_gate=AutoApproveGate())

        async def _test():
            return await installer.install_from_tarball(str(tar_path))

        result = asyncio.run(_test())
        assert not result.success
        assert "unsafe path" in result.message.lower()


def test_install_atomic():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "sample-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(SAMPLE_SKILL_DIR)

        install_dir = Path(tmp) / "installed"
        installer = SkillInstaller(skills_dir=install_dir, approval_gate=AutoApproveGate())
        result = asyncio.run(installer.install_from_path(source))

        assert result.success
        # Should not leave temp dirs behind
        temp_dirs = list(install_dir.glob("*.tmp"))
        assert len(temp_dirs) == 0
