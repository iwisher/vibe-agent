"""Validate skills and detect security risks."""
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import Skill

# Pre-compiled patterns for performance
_FS_DANGEROUS_PATTERNS = [
    (re.compile(r"rm\s+-rf\s+/+", re.IGNORECASE), "filesystem destruction: rm -rf /"),
    (re.compile(r"rm\s+-rf\s+~", re.IGNORECASE), "filesystem destruction: rm -rf home directory"),
    (re.compile(r">\s*/dev/sda", re.IGNORECASE), "disk overwrite attack"),
    (re.compile(r"dd\s+if=/dev/zero\s+of=/dev/[sh]d", re.IGNORECASE), "disk destruction"),
    (re.compile(r"chmod\s+[-+]?[0-7]*777\s+/+", re.IGNORECASE), "dangerous chmod"),
    (re.compile(r"\bsudo\b", re.IGNORECASE), "privilege escalation: sudo"),
    (re.compile(r"\bsu\b", re.IGNORECASE), "privilege escalation: su"),
    (re.compile(r"\bdoas\b", re.IGNORECASE), "privilege escalation: doas"),
]

_PHISHING_PATTERNS = [
    (re.compile(r"(curl|wget|fetch)\s+[^|]*\|\s*(bash|sh|zsh|python|perl|ruby)", re.IGNORECASE), "pipe-to-shell attack"),
    (re.compile(r"bash\s+.*<\s*\(\s*(curl|wget|fetch)", re.IGNORECASE), "process substitution attack"),
    (re.compile(r"eval\s*\(", re.IGNORECASE), "eval injection"),
    (re.compile(r"eval\s+[`\"']", re.IGNORECASE), "eval injection"),
    (re.compile(r"\beval\s+\$", re.IGNORECASE), "eval injection"),
]

_SUSPICIOUS_URLS = [
    re.compile(r"https?://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", re.IGNORECASE),
    re.compile(r"https?://[^/]*evil", re.IGNORECASE),
    re.compile(r"https?://[^/]*malicious", re.IGNORECASE),
    re.compile(r"https?://[^/]*phish", re.IGNORECASE),
]

_SUSPICIOUS_APIS = [
    re.compile(r"api\.key\s*=", re.IGNORECASE),
    re.compile(r"api_key\s*=", re.IGNORECASE),
    re.compile(r"token\s*=", re.IGNORECASE),
    re.compile(r"password\s*=", re.IGNORECASE),
    re.compile(r"secret\s*=", re.IGNORECASE),
]


@dataclass
class ValidationResult:
    is_valid: bool = True
    risks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_risk(self, message: str) -> None:
        self.is_valid = False
        self.risks.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


class SkillValidator:
    """Validate a skill and detect security risks."""

    def validate(self, skill: Skill, skill_dir: Path | None = None) -> ValidationResult:
        result = ValidationResult()

        # Check required fields
        if not skill.steps:
            result.add_risk("Skill has no steps")

        # Check each step for security issues
        for step in skill.steps:
            self._check_command_security(step, result)

        # Scan scripts directory recursively
        if skill_dir:
            self._scan_scripts(skill_dir, result)

        return result

    def _check_command_security(self, step, result: ValidationResult) -> None:
        command = step.command or ""

        # Filesystem risks
        for pattern, description in _FS_DANGEROUS_PATTERNS:
            if pattern.search(command):
                result.add_risk(f"Step '{step.id}': {description}")

        # Phishing / pipe-to-shell
        for pattern, description in _PHISHING_PATTERNS:
            if pattern.search(command):
                result.add_risk(f"Step '{step.id}': {description}")

        # Suspicious URLs
        for pattern in _SUSPICIOUS_URLS:
            if pattern.search(command):
                result.add_risk(f"Step '{step.id}': suspicious URL detected")

        # Suspicious API patterns
        for pattern in _SUSPICIOUS_APIS:
            if pattern.search(command):
                result.add_warning(f"Step '{step.id}': potential hardcoded credential")

    def _scan_scripts(self, skill_dir: Path, result: ValidationResult) -> None:
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.exists():
            return
        # Scan scripts directory recursively
        for script_file in scripts_dir.rglob("*"):
            if not script_file.is_file():
                continue
            try:
                content = script_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            # Scan with same patterns
            for pattern, description in _FS_DANGEROUS_PATTERNS:
                if pattern.search(content):
                    result.add_risk(f"Script '{script_file.name}': {description}")

            for pattern, description in _PHISHING_PATTERNS:
                if pattern.search(content):
                    result.add_risk(f"Script '{script_file.name}': {description}")

            for pattern in _SUSPICIOUS_URLS:
                if pattern.search(content):
                    result.add_risk(f"Script '{script_file.name}': suspicious URL detected")
