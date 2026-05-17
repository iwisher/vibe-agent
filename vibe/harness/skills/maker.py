"""Autonomous skill generation pipeline (Phase 4.2)."""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibe.harness.skills.approval import ApprovalGate, AutoRejectGate
from vibe.harness.skills.installer import InstallResult, SkillInstaller
from vibe.harness.skills.maker_config import SkillMakerConfig
from vibe.harness.skills.maker_prompts import build_skill_generation_prompt
from vibe.harness.skills.models import Skill
from vibe.harness.skills.parser import SkillParser
from vibe.harness.skills.validator import SkillValidator, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class DetectedPattern:
    """A recurring pattern detected from wiki pages."""

    pattern_id: str
    tag: str
    page_titles: list[str]
    page_ids: list[str]
    frequency: int
    suggested_tools: list[str]
    confidence: float  # 0.0-1.0 based on frequency and distinct sessions


@dataclass
class SkillProposal:
    """A proposed skill with metadata."""

    proposal_id: str
    pattern: DetectedPattern
    skill_draft: Skill
    raw_markdown: str
    validation_result: ValidationResult
    proposed_at: str
    approved: bool | None = None  # None = pending


class SkillMakerPipeline:
    """Detects recurring patterns from wiki and generates skill proposals."""

    def __init__(
        self,
        config: SkillMakerConfig | None = None,
        wiki: Any | None = None,
        llm_client: Any | None = None,
        skill_installer: SkillInstaller | None = None,
        approval_gate: ApprovalGate | None = None,
    ):
        self.config = config or SkillMakerConfig()
        self.wiki = wiki
        self.llm = llm_client
        self.installer = skill_installer
        self.approval_gate = approval_gate or AutoRejectGate()
        self.parser = SkillParser()
        self.validator = SkillValidator()
        self._proposals: list[SkillProposal] = []
        self._proposals_this_session = 0

    async def detect_patterns(self) -> list[DetectedPattern]:
        """Scan wiki for recurring patterns based on tags and titles."""
        if self.wiki is None:
            return []

        # Collect all pages and group by tag
        tag_to_pages: dict[str, list[dict]] = {}
        try:
            pages = await self.wiki.list_pages()
        except Exception as e:
            logger.warning("SkillMaker: wiki.list_pages() failed: %s", e)
            return []

        for page in pages:
            for tag in page.tags:
                if tag in self.config.excluded_tags:
                    continue
                tag_to_pages.setdefault(tag, []).append(
                    {
                        "title": page.title,
                        "id": page.id,
                        "content": page.content[:500],  # truncated for analysis
                    }
                )

        patterns = []
        for tag, pages in tag_to_pages.items():
            if len(pages) < self.config.min_pattern_frequency:
                continue

            # Extract suggested tools from page content
            suggested_tools = self._extract_tools_from_pages(pages)

            # Confidence: frequency + distinct page ratio
            confidence = min(1.0, len(pages) / self.config.min_pattern_frequency * 0.3 + 0.4)

            patterns.append(
                DetectedPattern(
                    pattern_id=f"pattern_{tag}_{uuid.uuid4().hex[:8]}",
                    tag=tag,
                    page_titles=[p["title"] for p in pages],
                    page_ids=[p["id"] for p in pages],
                    frequency=len(pages),
                    suggested_tools=suggested_tools,
                    confidence=confidence,
                )
            )

        # Sort by confidence desc
        patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns

    def _extract_tools_from_pages(self, pages: list[dict]) -> list[str]:
        """Heuristic: extract tool names mentioned in page content."""
        tools = set()
        tool_patterns = {
            "bash": r"\bbash\b|\bshell\b|\bsh\b",
            "file_write": r"\bfile_write\b|\bwrite_file\b|\bwritefile\b",
            "file_read": r"\bfile_read\b|\bread_file\b|\breadfile\b",
            "git_commit": r"\bgit_commit\b|\bgit\s+commit\b",
            "git_push": r"\bgit_push\b|\bgit\s+push\b",
        }
        for page in pages:
            content = page.get("content", "").lower()
            for tool, pattern in tool_patterns.items():
                if re.search(pattern, content):
                    tools.add(tool)
        return sorted(tools)

    async def generate_skill(self, pattern: DetectedPattern) -> SkillProposal | None:
        """Use LLM to generate a skill draft from a detected pattern."""
        if self.llm is None:
            logger.warning("SkillMaker: no LLM client available")
            return None

        if self._proposals_this_session >= self.config.max_skills_per_session:
            logger.info("SkillMaker: max skills per session reached")
            return None

        skill_id = f"auto_{pattern.tag}_{uuid.uuid4().hex[:8]}"
        skill_name = f"Auto: {pattern.tag.replace('_', ' ').title()}"
        description = (
            f"Automatically generated skill for {pattern.tag} tasks. "
            f"Detected across {pattern.frequency} wiki pages."
        )
        category = pattern.tag

        prompt = build_skill_generation_prompt(
            skill_id=skill_id,
            skill_name=skill_name,
            description=description,
            category=category,
            tags=[pattern.tag],
            patterns=[f"*{pattern.tag}*"],
            required_tools=pattern.suggested_tools,
            pattern_summary=self._build_pattern_summary(pattern),
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm.complete(messages)
            raw_markdown = response.content or ""
        except Exception as e:
            logger.warning("SkillMaker: LLM generation failed: %s", e)
            return None

        # Parse the generated skill
        try:
            skill = self.parser.parse_string(raw_markdown)
        except Exception as e:
            logger.warning("SkillMaker: generated skill parse failed: %s", e)
            return None

        # Validate
        validation = self.validator.validate(skill)

        proposal = SkillProposal(
            proposal_id=f"proposal_{uuid.uuid4().hex[:8]}",
            pattern=pattern,
            skill_draft=skill,
            raw_markdown=raw_markdown,
            validation_result=validation,
            proposed_at=datetime.now(timezone.utc).isoformat(),
        )

        self._proposals.append(proposal)
        self._proposals_this_session += 1
        return proposal

    def _build_pattern_summary(self, pattern: DetectedPattern) -> str:
        """Build a sanitized summary of the pattern for the LLM prompt."""
        # Sanitize titles to prevent prompt injection
        safe_titles = []
        for title in pattern.page_titles[:5]:
            # Remove newlines, backslashes, and control chars that could break prompts
            safe = title.replace("\\", "").replace("\n", " ").replace("\r", " ")
            safe = safe.replace("+++", "").replace('"', "'")
            safe_titles.append(safe)

        lines = [
            f"Tag: {pattern.tag}",
            f"Frequency: {pattern.frequency} wiki pages",
            f"Pages: {', '.join(safe_titles)}",
            f"Suggested tools: {', '.join(pattern.suggested_tools)}",
        ]
        return "\n".join(lines)

    async def propose_installation(self, proposal: SkillProposal) -> InstallResult | None:
        """Propose the skill for installation via the approval gate."""
        if self.installer is None:
            logger.warning("SkillMaker: no installer available")
            return None

        # Approval gate check
        risks = proposal.validation_result.risks.copy()
        warnings = proposal.validation_result.warnings.copy()

        # Add auto-generation warning
        warnings.append("This skill was automatically generated by the SkillMaker pipeline")

        approved = self.approval_gate.approve(
            skill_name=proposal.skill_draft.name,
            risks=risks,
            warnings=warnings,
        )

        proposal.approved = approved

        if not approved:
            logger.info("SkillMaker: proposal %s rejected by approval gate", proposal.proposal_id)
            return InstallResult(success=False, message="Rejected by approval gate")

        # Auto-install if no risks and config allows
        if self.config.auto_install_approved and not risks:
            # Write to temp dir and install
            with tempfile.TemporaryDirectory() as tmp:
                skill_dir = Path(tmp) / proposal.skill_draft.id
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(proposal.raw_markdown, encoding="utf-8")
                result = await self.installer.install_from_path(skill_dir)
                return result

        logger.info("SkillMaker: proposal %s approved but not auto-installed", proposal.proposal_id)
        return InstallResult(
            success=True,
            message=(
                f"Skill '{proposal.skill_draft.id}' approved. "
                "Install with: vibe skill install --path <dir>"
            ),
            skill_id=proposal.skill_draft.id,
        )

    async def run_once(self) -> list[SkillProposal]:
        """Run one detection + generation cycle. Returns proposals created."""
        if not self.config.enabled:
            return []

        patterns = await self.detect_patterns()
        if not patterns:
            return []

        proposals = []
        for pattern in patterns[: self.config.max_skills_per_session]:
            proposal = await self.generate_skill(pattern)
            if proposal:
                await self.propose_installation(proposal)
                proposals.append(proposal)

        return proposals

    def reset_session(self) -> None:
        """Reset per-session counters. Call at start of new session."""
        self._proposals_this_session = 0
