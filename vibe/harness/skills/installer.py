"""Install skills from git repos, tarballs, or local paths."""
import asyncio
import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .approval import ApprovalGate, AutoRejectGate
from .parser import SkillParser
from .validator import SkillValidator


@dataclass
class InstallResult:
    success: bool
    message: str
    skill_id: str | None = None
    path: Path | None = None


class SkillInstaller:
    """Install vibe skills with security checks."""

    def __init__(
        self,
        skills_dir: Path | str = "~/.vibe/skills",
        approval_gate: ApprovalGate | None = None,
    ):
        self.skills_dir = Path(skills_dir).expanduser().resolve()
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.parser = SkillParser()
        self.validator = SkillValidator()
        self.approval_gate = approval_gate or AutoRejectGate()

    async def install_from_git(
        self, url: str, skill_id: str | None = None
    ) -> InstallResult:
        """Install from a git repository."""
        with tempfile.TemporaryDirectory() as tmp:
            clone_dir = Path(tmp) / "skill"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--depth", "1", "--", url, str(clone_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                if proc.returncode != 0:
                    return InstallResult(
                        success=False,
                        message=f"Git clone failed: {stderr.decode()}",
                    )
            except asyncio.TimeoutError:
                return InstallResult(success=False, message="Git clone timed out after 60s")
            except Exception as e:
                return InstallResult(success=False, message=f"Git clone error: {e}")

            return await self._install_from_directory(clone_dir, skill_id)

    async def install_from_tarball(
        self, url_or_path: str, skill_id: str | None = None
    ) -> InstallResult:
        """Install from a tarball URL or local path."""
        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / "skill.tar.gz"

            if url_or_path.startswith("http"):
                # Download via async thread pool to avoid blocking
                import urllib.request
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(urllib.request.urlretrieve, url_or_path, tar_path),
                        timeout=60,
                    )
                except asyncio.TimeoutError:
                    return InstallResult(success=False, message="Download timed out after 60s")
                except Exception as e:
                    return InstallResult(success=False, message=f"Download failed: {e}")
            else:
                tar_path = Path(url_or_path).expanduser().resolve()
                if not tar_path.exists():
                    return InstallResult(success=False, message=f"Tarball not found: {tar_path}")

            extract_dir = Path(tmp) / "extracted"
            extract_dir.mkdir()

            try:
                with tarfile.open(tar_path, "r:gz") as tf:
                    # Zip Slip protection: validate all member paths
                    for member in tf.getmembers():
                        member_path = extract_dir / member.name
                        try:
                            member_path.resolve().relative_to(extract_dir.resolve())
                        except ValueError:
                            return InstallResult(
                                success=False,
                                message=f"Tarball contains unsafe path: {member.name}",
                            )
                    tf.extractall(extract_dir)
            except Exception as e:
                return InstallResult(success=False, message=f"Extraction failed: {e}")

            # Find the skill directory (first subdirectory with SKILL.md)
            skill_dir = None
            for item in extract_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    skill_dir = item
                    break

            if skill_dir is None:
                return InstallResult(success=False, message="No SKILL.md found in tarball")

            return await self._install_from_directory(skill_dir, skill_id)

    async def install_from_path(
        self, source: Path, skill_id: str | None = None
    ) -> InstallResult:
        """Install from a local directory."""
        return await self._install_from_directory(source, skill_id)

    async def _install_from_directory(
        self, source: Path, skill_id: str | None = None
    ) -> InstallResult:
        skill_file = source / "SKILL.md"
        if not skill_file.exists():
            return InstallResult(success=False, message=f"No SKILL.md found in {source}")

        # Parse and validate
        try:
            skill = self.parser.parse_file(skill_file)
        except Exception as e:
            return InstallResult(success=False, message=f"Parse error: {e}")

        validation = self.validator.validate(skill, skill_dir=source)

        # Approval gate
        if validation.risks or validation.warnings:
            approved = self.approval_gate.approve(
                skill_name=skill.name,
                risks=validation.risks,
                warnings=validation.warnings,
            )
            if not approved:
                return InstallResult(
                    success=False,
                    message="Installation rejected by approval gate",
                    skill_id=skill.id,
                )

        # Determine target directory
        target_id = skill_id or skill.id
        if not target_id:
            return InstallResult(success=False, message="Skill ID is required")

        target_dir = self.skills_dir / target_id

        if target_dir.exists():
            approved = self.approval_gate.approve(
                skill_name=skill.name,
                risks=[f"Skill '{target_id}' already exists"],
                warnings=[],
            )
            if not approved:
                return InstallResult(
                    success=False,
                    message="Installation cancelled (skill exists)",
                    skill_id=target_id,
                )
            shutil.rmtree(target_dir)

        # Atomic install: copy to temp, then rename
        temp_dir = self.skills_dir / f"{target_id}.tmp"
        try:
            shutil.copytree(
                source,
                temp_dir,
                ignore=shutil.ignore_patterns(".git", "*.pyc", "__pycache__"),
            )
            temp_dir.rename(target_dir)
        except Exception as e:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            return InstallResult(success=False, message=f"Install failed: {e}")

        # Update index
        self._update_index(target_id, skill)

        return InstallResult(
            success=True,
            message=f"Skill '{target_id}' installed successfully",
            skill_id=target_id,
            path=target_dir,
        )

    def _update_index(self, skill_id: str, skill) -> None:
        index_file = self.skills_dir / "index.json"
        index = {}
        if index_file.exists():
            try:
                index = json.loads(index_file.read_text())
            except json.JSONDecodeError:
                index = {}

        index.setdefault("skills", {})[skill_id] = {
            "version": skill.vibe_skill_version,
            "path": str(self.skills_dir / skill_id),
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "validated": True,
        }

        index_file.write_text(json.dumps(index, indent=2))

    def list_installed(self) -> dict[str, dict]:
        index_file = self.skills_dir / "index.json"
        if not index_file.exists():
            return {}
        try:
            return json.loads(index_file.read_text()).get("skills", {})
        except json.JSONDecodeError:
            return {}

    async def uninstall(self, skill_id: str) -> InstallResult:
        """Remove an installed skill."""
        target_dir = self.skills_dir / skill_id
        if not target_dir.exists():
            return InstallResult(
                success=False,
                message=f"Skill '{skill_id}' not found",
            )

        try:
            shutil.rmtree(target_dir)
        except Exception as e:
            return InstallResult(
                success=False,
                message=f"Failed to remove skill: {e}",
            )

        # Update index
        index_file = self.skills_dir / "index.json"
        if index_file.exists():
            try:
                index = json.loads(index_file.read_text())
                index.get("skills", {}).pop(skill_id, None)
                index_file.write_text(json.dumps(index, indent=2))
            except json.JSONDecodeError:
                pass

        return InstallResult(
            success=True,
            message=f"Skill '{skill_id}' uninstalled",
            skill_id=skill_id,
        )
