"""Autonomous skill generation pipeline (Phase 4.2).

Also hosts the lesson→skill promotion path (Workstream B4): a principle-level
``procedure`` lesson page (persisted generality >= 4, net counters
helpful - harmful >= 2, not archived) qualifies for compilation into a v2
script-backed skill (the stock-analysis pattern). Acceptance gate: the existing
SkillValidator scan plus one smoke-run of the script's verification via the
skill runner when fixture inputs are derivable, then the normal approval gate.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibe.harness.skills.approval import ApprovalGate, AutoRejectGate
from vibe.harness.skills.installer import InstallResult, SkillInstaller
from vibe.harness.skills.maker_config import SkillMakerConfig
from vibe.harness.skills.maker_prompts import (
    build_lesson_skill_prompt,
    build_skill_generation_prompt,
)
from vibe.harness.skills.models import Skill
from vibe.harness.skills.parser import SkillParser
from vibe.harness.skills.validator import SkillValidator, ValidationResult
from vibe.memory.reflection import _read_counter, _read_generality, _strip_counters

logger = logging.getLogger(__name__)

# Lesson→skill promotion thresholds (B4): principle-level, net-positive lessons.
_PROMOTION_MIN_GENERALITY = 4
_PROMOTION_MIN_NET_COUNTER = 2

# Delimiter separating the SKILL.md body from bundled script blocks in the
# LLM's lesson-promotion output: `=== scripts/run.py ===` on its own line.
_BUNDLE_SCRIPT_RE = re.compile(r"^===\s*(scripts/[^\s=]+?)\s*===\s*$", re.MULTILINE)

# Lesson content bound in the promotion prompt.
_LESSON_PROMPT_MAX_CHARS = 2000


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
    # On-disk draft directory (SKILL.md + scripts/) for script-backed skills
    staged_dir: str | None = None


class SkillMakerPipeline:
    """Detects recurring patterns from wiki and generates skill proposals."""

    def __init__(
        self,
        config: SkillMakerConfig | None = None,
        wiki: Any | None = None,
        llm_client: Any | None = None,
        skill_installer: SkillInstaller | None = None,
        approval_gate: ApprovalGate | None = None,
        staging_dir: str | Path | None = None,
    ):
        self.config = config or SkillMakerConfig()
        self.wiki = wiki
        self.llm = llm_client
        self.installer = skill_installer
        self.approval_gate = approval_gate or AutoRejectGate()
        self.parser = SkillParser()
        self.validator = SkillValidator()
        # Staging area for on-disk skill drafts (lesson promotion writes
        # SKILL.md + scripts/ here so they can be validated and smoke-run)
        self.staging_dir = (
            Path(staging_dir).expanduser()
            if staging_dir
            else Path.home() / ".vibe" / "skill_maker_staging"
        )
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

    # ------------------------------------------------------------------
    # Lesson→skill promotion (B4)
    # ------------------------------------------------------------------

    async def detect_lesson_candidates(self) -> list[Any]:
        """Lesson wiki pages eligible for promotion to executable skills."""
        if self.wiki is None:
            return []
        try:
            pages = await self.wiki.list_pages(tag="lesson")
        except Exception as e:
            logger.warning("SkillMaker: wiki.list_pages(tag='lesson') failed: %s", e)
            return []
        return [p for p in pages if self._qualifies_for_promotion(p)]

    @staticmethod
    def _qualifies_for_promotion(page: Any) -> bool:
        """Qualification gate: procedure kind, generality >= 4, net counters
        helpful - harmful >= 2, status draft/verified (not archived)."""
        tags = getattr(page, "tags", None) or []
        if "lesson" not in tags or "procedure" not in tags:
            return False
        if getattr(page, "status", None) not in ("draft", "verified"):
            return False
        content = getattr(page, "content", "") or ""
        generality = _read_generality(content)
        if generality is None or generality < _PROMOTION_MIN_GENERALITY:
            return False
        net = _read_counter(content, "helpful") - _read_counter(content, "harmful")
        return net >= _PROMOTION_MIN_NET_COUNTER

    async def generate_skill_from_lesson(self, page: Any) -> SkillProposal | None:
        """Generate a v2 script-backed skill draft from a qualifying lesson page.

        The LLM emits SKILL.md plus bundled script blocks; the draft is staged
        on disk, scanned by the SkillValidator, and smoke-run once via the
        skill runner when fixture inputs are derivable. Returns None (no
        proposal) on any generation, staging, or smoke-run failure.
        """
        if self.llm is None:
            logger.warning("SkillMaker: no LLM client available")
            return None
        if self._proposals_this_session >= self.config.max_skills_per_session:
            logger.info("SkillMaker: max skills per session reached")
            return None

        skill_id = f"lesson_{uuid.uuid4().hex[:8]}"
        title = str(getattr(page, "title", ""))
        safe_title = title.replace("\\", "").replace("\n", " ").replace("\r", " ")
        content = _strip_counters(getattr(page, "content", ""))
        if len(content) > _LESSON_PROMPT_MAX_CHARS:
            content = content[: _LESSON_PROMPT_MAX_CHARS - 1].rstrip() + "…"
        prompt = build_lesson_skill_prompt(
            skill_id=skill_id, lesson_title=safe_title, lesson_content=content
        )

        try:
            response = await self.llm.complete([{"role": "user", "content": prompt}])
            raw = response.content or ""
        except Exception as e:
            logger.warning("SkillMaker: lesson skill generation failed: %s", e)
            return None

        bundle = self._split_skill_bundle(raw)
        if bundle is None:
            logger.warning("SkillMaker: lesson skill output missing SKILL.md or script block")
            return None
        skill_md, scripts = bundle

        try:
            skill = self.parser.parse_string(skill_md)
        except Exception as e:
            logger.warning("SkillMaker: generated lesson skill parse failed: %s", e)
            return None

        # Stage the draft on disk (SKILL.md + scripts/) so the validator can
        # scan the scripts and the runner can resolve script paths.
        staged = self.staging_dir / skill.id
        try:
            if staged.exists():
                shutil.rmtree(staged)
            staged.mkdir(parents=True)
            (staged / "SKILL.md").write_text(skill_md, encoding="utf-8")
            for rel_path, script_text in scripts.items():
                target = staged / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(script_text, encoding="utf-8")
        except OSError as e:
            logger.warning("SkillMaker: failed to stage lesson skill draft: %s", e)
            return None
        skill.skill_dir = str(staged)

        # Acceptance gate 1: existing SkillValidator scan (scripts included)
        validation = self.validator.validate(skill, skill_dir=staged)

        # Acceptance gate 2: smoke-run the verification once when fixture
        # inputs are derivable (every required variable has a default).
        if validation.is_valid:
            fixtures = self._derivable_fixture_variables(skill)
            if fixtures is not None:
                if not await self._smoke_run(skill, fixtures):
                    logger.info(
                        "SkillMaker: lesson skill '%s' blocked by smoke-run failure", skill.id
                    )
                    return None

        generality = _read_generality(getattr(page, "content", "") or "")
        pattern = DetectedPattern(
            pattern_id=f"lesson_{getattr(page, 'id', 'unknown')}",
            tag="lesson",
            page_titles=[title],
            page_ids=[str(getattr(page, "id", ""))],
            frequency=1,
            suggested_tools=["bash"],
            confidence=min(1.0, (generality or 3) / 5),
        )
        proposal = SkillProposal(
            proposal_id=f"proposal_{uuid.uuid4().hex[:8]}",
            pattern=pattern,
            skill_draft=skill,
            raw_markdown=skill_md,
            validation_result=validation,
            proposed_at=datetime.now(timezone.utc).isoformat(),
            staged_dir=str(staged),
        )
        self._proposals.append(proposal)
        self._proposals_this_session += 1
        return proposal

    @staticmethod
    def _split_skill_bundle(raw: str) -> tuple[str, dict[str, str]] | None:
        """Split LLM output into (SKILL.md text, {script path: content}).

        Returns None when the SKILL.md part or all script blocks are missing,
        or when a script path escapes scripts/.
        """
        text = (raw or "").strip()
        matches = list(_BUNDLE_SCRIPT_RE.finditer(text))
        if not matches:
            return None
        skill_md = text[: matches[0].start()].strip()
        if not skill_md.startswith("+++"):
            return None
        scripts: dict[str, str] = {}
        for i, match in enumerate(matches):
            rel_path = match.group(1)
            if ".." in Path(rel_path).parts:
                return None
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            scripts[rel_path] = text[match.end() : end].strip()
        return skill_md, scripts

    @staticmethod
    def _derivable_fixture_variables(skill: Skill) -> dict[str, Any] | None:
        """Fixture inputs for a smoke run: derivable iff every required
        variable declares a default. None when not derivable."""
        fixtures: dict[str, Any] = {}
        for var in skill.variables:
            name = var.get("name")
            if not name:
                continue
            if "default" in var:
                fixtures[name] = var["default"]
            elif var.get("required"):
                return None
        return fixtures

    async def _smoke_run(self, skill: Skill, variables: dict[str, Any]) -> bool:
        """Run the staged skill once via the skill runner; True on success.

        Uses a fresh ToolSystem with a sandboxed BashTool rooted at the staged
        draft directory. Never raises — errors count as failure.
        """
        try:
            from vibe.tools.bash import BashSandbox, BashTool
            from vibe.tools.skill_runner import SkillRunnerTool
            from vibe.tools.tool_system import ToolSystem

            tools = ToolSystem()
            tools.register_tool(
                BashTool(
                    sandbox=BashSandbox(
                        working_dir=skill.skill_dir or str(self.staging_dir),
                        timeout=self.config.sandbox_timeout_seconds,
                    )
                )
            )
            runner = SkillRunnerTool({skill.id: skill}, tools)
            result = await runner.execute(skill_id=skill.id, variables=variables)
            return bool(result.success)
        except Exception as e:
            logger.warning("SkillMaker: smoke run error for '%s': %s", skill.id, e)
            return False

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

        approved = await asyncio.to_thread(
            self.approval_gate.approve,
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
            if proposal.staged_dir:
                # Script-backed drafts install from the staged directory so
                # the bundled scripts/ come along.
                return await self.installer.install_from_path(Path(proposal.staged_dir))
            # Write to temp dir and install
            with tempfile.TemporaryDirectory() as tmp:
                skill_dir = Path(tmp) / proposal.skill_draft.id
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(proposal.raw_markdown, encoding="utf-8")
                result = await self.installer.install_from_path(skill_dir)
                return result

        logger.info("SkillMaker: proposal %s approved but not auto-installed", proposal.proposal_id)
        install_path = proposal.staged_dir or "<dir>"
        return InstallResult(
            success=True,
            message=(
                f"Skill '{proposal.skill_draft.id}' approved. "
                f"Install with: vibe skill install --path {install_path}"
            ),
            skill_id=proposal.skill_draft.id,
        )

    async def run_once(self) -> list[SkillProposal]:
        """Run one detection + generation cycle. Returns proposals created."""
        if not self.config.enabled:
            return []

        patterns = await self.detect_patterns()
        proposals = []
        for pattern in patterns[: self.config.max_skills_per_session]:
            proposal = await self.generate_skill(pattern)
            if proposal:
                await self.propose_installation(proposal)
                proposals.append(proposal)

        # Lesson→skill promotion: compile qualifying principle-level procedure
        # lessons into script-backed skills (respects the same session cap).
        if self._proposals_this_session < self.config.max_skills_per_session:
            for page in await self.detect_lesson_candidates():
                if self._proposals_this_session >= self.config.max_skills_per_session:
                    break
                proposal = await self.generate_skill_from_lesson(page)
                if proposal:
                    await self.propose_installation(proposal)
                    proposals.append(proposal)

        return proposals

    def reset_session(self) -> None:
        """Reset per-session counters. Call at start of new session."""
        self._proposals_this_session = 0
