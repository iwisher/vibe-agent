"""Vibe-native skill system."""
# Type aliases to disambiguate the two Skill classes
from vibe.harness.instructions import Skill as PromptSkill  # YAML, prompt-based

from .approval import ApprovalGate, AutoApproveGate, AutoRejectGate, CLIApprovalGate
from .executor import ExecutionResult, SkillExecutor
from .installer import InstallResult, SkillInstaller
from .models import Skill, SkillStep, SkillTrigger, SkillVerification
from .parser import SkillParser
from .validator import SkillValidator, ValidationResult

ExecutableSkill = Skill  # vibe.harness.skills.models.Skill (TOML, executable)

__all__ = [
    "Skill",
    "SkillStep",
    "SkillTrigger",
    "SkillVerification",
    "SkillParser",
    "SkillValidator",
    "ValidationResult",
    "ApprovalGate",
    "CLIApprovalGate",
    "AutoApproveGate",
    "AutoRejectGate",
    "SkillInstaller",
    "InstallResult",
    "SkillExecutor",
    "ExecutionResult",
    "PromptSkill",
    "ExecutableSkill",
]
