"""Tests for SkillMakerPipeline."""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.llm_types import LLMResponse
from vibe.harness.skills.approval import AutoApproveGate, AutoRejectGate
from vibe.harness.skills.installer import InstallResult, SkillInstaller
from vibe.harness.skills.maker import DetectedPattern, SkillMakerPipeline, SkillProposal
from vibe.harness.skills.maker_config import SkillMakerConfig
from vibe.harness.skills.models import Skill, SkillStep, SkillTrigger
from vibe.harness.skills.validator import ValidationResult


@dataclass
class MockWikiPage:
    """Simple mock wiki page for testing."""

    id: str
    title: str
    content: str
    tags: list[str]


@pytest.fixture
def mock_wiki():
    """Return a mock wiki with an async list_pages method."""
    wiki = MagicMock()
    wiki.list_pages = AsyncMock(return_value=[])
    return wiki


@pytest.fixture
def mock_llm():
    """Return a mock LLM client."""
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="""+++
vibe_skill_version = "1.0"
id = "auto_test_tag_abc12345"
name = "Auto: Test Tag"
description = "Automatically generated skill for test_tag tasks. Detected across 3 wiki pages."
category = "test_tag"
tags = ["test_tag"]

[[steps]]
id = "step_1"
description = "Run a bash command"
tool = "bash"
command = "echo hello"

[trigger]
patterns = ["*test_tag*"]
required_tools = ["bash"]
+++

## Pitfalls
- None

## Examples
### Example 1:
**Input:** test
**Expected:** hello
"""
        )
    )
    return llm


@pytest.fixture
def mock_installer():
    """Return a mock skill installer."""
    installer = MagicMock(spec=SkillInstaller)
    installer.install_from_path = AsyncMock(
        return_value=InstallResult(
            success=True, message="Installed", skill_id="auto_test_tag_abc12345"
        )
    )
    return installer


class TestDetectPatterns:
    @pytest.mark.asyncio
    async def test_detect_patterns_empty_wiki(self, mock_wiki):
        """Empty wiki returns no patterns."""
        pipeline = SkillMakerPipeline(wiki=mock_wiki)
        patterns = await pipeline.detect_patterns()
        assert patterns == []

    @pytest.mark.asyncio
    async def test_detect_patterns_single_tag(self, mock_wiki):
        """Tag with enough pages returns a pattern."""
        mock_wiki.list_pages.return_value = [
            MockWikiPage("p1", "Page 1", "Use bash to run tests", ["testing"]),
            MockWikiPage("p2", "Page 2", "Use bash to lint code", ["testing"]),
            MockWikiPage("p3", "Page 3", "Use bash to deploy", ["testing"]),
        ]
        pipeline = SkillMakerPipeline(wiki=mock_wiki)
        patterns = await pipeline.detect_patterns()
        assert len(patterns) == 1
        assert patterns[0].tag == "testing"
        assert patterns[0].frequency == 3
        assert "bash" in patterns[0].suggested_tools

    @pytest.mark.asyncio
    async def test_detect_patterns_excluded_tags(self, mock_wiki):
        """Excluded tags are filtered out."""
        mock_wiki.list_pages.return_value = [
            MockWikiPage("p1", "Page 1", "content", ["session"]),
            MockWikiPage("p2", "Page 2", "content", ["session"]),
            MockWikiPage("p3", "Page 3", "content", ["session"]),
        ]
        pipeline = SkillMakerPipeline(wiki=mock_wiki)
        patterns = await pipeline.detect_patterns()
        assert patterns == []

    @pytest.mark.asyncio
    async def test_detect_patterns_insufficient_frequency(self, mock_wiki):
        """Below threshold returns empty."""
        mock_wiki.list_pages.return_value = [
            MockWikiPage("p1", "Page 1", "content", ["rare"]),
            MockWikiPage("p2", "Page 2", "content", ["rare"]),
        ]
        pipeline = SkillMakerPipeline(wiki=mock_wiki)
        patterns = await pipeline.detect_patterns()
        assert patterns == []


class TestExtractTools:
    def test_extract_tools_from_pages(self):
        """Heuristic tool extraction from page content."""
        pipeline = SkillMakerPipeline()
        pages = [
            {"content": "Run bash script to deploy"},
            {"content": "Use file_write to create config"},
            {"content": "Read file_read for logs"},
            {"content": "git_commit and git_push changes"},
        ]
        tools = pipeline._extract_tools_from_pages(pages)
        assert "bash" in tools
        assert "file_write" in tools
        assert "file_read" in tools
        assert "git_commit" in tools
        assert "git_push" in tools


class TestGenerateSkill:
    @pytest.mark.asyncio
    async def test_generate_skill_no_llm(self):
        """Returns None when no LLM is configured."""
        pipeline = SkillMakerPipeline()
        pattern = DetectedPattern(
            pattern_id="pat_1",
            tag="test",
            page_titles=["T1"],
            page_ids=["p1"],
            frequency=3,
            suggested_tools=["bash"],
            confidence=0.8,
        )
        result = await pipeline.generate_skill(pattern)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_skill_max_per_session(self, mock_llm):
        """Respects max_skills_per_session."""
        config = SkillMakerConfig(max_skills_per_session=1)
        pipeline = SkillMakerPipeline(config=config, llm_client=mock_llm)
        pattern = DetectedPattern(
            pattern_id="pat_1",
            tag="test",
            page_titles=["T1"],
            page_ids=["p1"],
            frequency=3,
            suggested_tools=["bash"],
            confidence=0.8,
        )
        # First call should succeed
        result1 = await pipeline.generate_skill(pattern)
        assert result1 is not None
        # Second call should be blocked
        result2 = await pipeline.generate_skill(pattern)
        assert result2 is None


class TestProposeInstallation:
    @pytest.mark.asyncio
    async def test_propose_installation_rejected(self, mock_installer):
        """AutoRejectGate blocks skills with risks."""
        config = SkillMakerConfig()
        pipeline = SkillMakerPipeline(
            config=config,
            skill_installer=mock_installer,
            approval_gate=AutoRejectGate(),
        )
        skill = Skill(
            vibe_skill_version="1.0",
            id="auto_test",
            name="Auto: Test",
            description="Test",
            category="test",
            trigger=SkillTrigger(),
            steps=[SkillStep(id="s1", description="x", tool="bash", command="rm -rf /")],
        )
        proposal = SkillProposal(
            proposal_id="prop_1",
            pattern=DetectedPattern(
                pattern_id="pat_1",
                tag="test",
                page_titles=["T1"],
                page_ids=["p1"],
                frequency=3,
                suggested_tools=["bash"],
                confidence=0.8,
            ),
            skill_draft=skill,
            raw_markdown="md",
            validation_result=ValidationResult(
                is_valid=False,
                risks=["Dangerous command"],
                warnings=[],
            ),
            proposed_at="2024-01-01T00:00:00+00:00",
        )
        result = await pipeline.propose_installation(proposal)
        assert result is not None
        assert result.success is False
        assert "Rejected by approval gate" in result.message

    @pytest.mark.asyncio
    async def test_propose_installation_auto_install(self, mock_installer):
        """auto_install_approved installs skills with no risks."""
        config = SkillMakerConfig(auto_install_approved=True)
        pipeline = SkillMakerPipeline(
            config=config,
            skill_installer=mock_installer,
            approval_gate=AutoApproveGate(),
        )
        skill = Skill(
            vibe_skill_version="1.0",
            id="auto_test",
            name="Auto: Test",
            description="Test",
            category="test",
            trigger=SkillTrigger(),
            steps=[SkillStep(id="s1", description="x", tool="bash", command="echo hello")],
        )
        proposal = SkillProposal(
            proposal_id="prop_1",
            pattern=DetectedPattern(
                pattern_id="pat_1",
                tag="test",
                page_titles=["T1"],
                page_ids=["p1"],
                frequency=3,
                suggested_tools=["bash"],
                confidence=0.8,
            ),
            skill_draft=skill,
            raw_markdown="md",
            validation_result=ValidationResult(
                is_valid=True,
                risks=[],
                warnings=[],
            ),
            proposed_at="2024-01-01T00:00:00+00:00",
        )
        result = await pipeline.propose_installation(proposal)
        assert result is not None
        assert result.success is True
        mock_installer.install_from_path.assert_awaited_once()


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_run_once_disabled(self):
        """Disabled config returns empty."""
        config = SkillMakerConfig(enabled=False)
        pipeline = SkillMakerPipeline(config=config)
        result = await pipeline.run_once()
        assert result == []


class TestBuildPatternSummary:
    def test_build_pattern_summary(self):
        """Summary formatting and sanitization."""
        pipeline = SkillMakerPipeline()
        pattern = DetectedPattern(
            pattern_id="pat_1",
            tag="test",
            page_titles=[
                "Normal Title",
                "Title with \\\\ backslash",
                "Title with \n newline",
                "Title with +++ quotes",
                'Title with "double" quotes',
            ],
            page_ids=["p1", "p2", "p3", "p4", "p5"],
            frequency=5,
            suggested_tools=["bash", "file_write"],
            confidence=0.9,
        )
        summary = pipeline._build_pattern_summary(pattern)
        assert "Tag: test" in summary
        assert "Frequency: 5 wiki pages" in summary
        # Sanitization checks
        assert "\\" not in summary
        assert "+++" not in summary
        assert '"' not in summary
        # Newlines inside titles should be replaced with spaces
        assert "Title with   newline" in summary or "Title with  newline" in summary
        assert "Suggested tools: bash, file_write" in summary


class TestResetSession:
    def test_reset_session(self, mock_llm):
        """Counters reset correctly."""
        config = SkillMakerConfig(max_skills_per_session=1)
        pipeline = SkillMakerPipeline(config=config, llm_client=mock_llm)
        pattern = DetectedPattern(
            pattern_id="pat_1",
            tag="test",
            page_titles=["T1"],
            page_ids=["p1"],
            frequency=3,
            suggested_tools=["bash"],
            confidence=0.8,
        )
        # Consume the single slot
        import asyncio

        result = asyncio.run(pipeline.generate_skill(pattern))
        assert result is not None
        assert pipeline._proposals_this_session == 1
        # Reset and verify
        pipeline.reset_session()
        assert pipeline._proposals_this_session == 0


# ---------------------------------------------------------------------------
# Lesson→skill promotion (B4)
# ---------------------------------------------------------------------------

_LESSON_BUNDLE = """+++
vibe_skill_version = "2.0.0"
id = "lesson_restart_abc12345"
name = "Restart Stale Dev Server"
description = "Restart the stale process holding a dev-server port before editing code"
category = "lesson"
tags = ["lesson"]

[[variables]]
name = "port"
type = "integer"
required = false
default = 8000
description = "Port the dev server should listen on"

[[steps]]
id = "run"
description = "Run the deterministic port-check script"
tool = "bash"
script = "scripts/run.py"
command = "{{ port }}"

[steps.verification]
exit_code = 0
json_has_keys = ["ok"]
+++

# Restart Stale Dev Server

## Overview
Checks whether a dev-server port is held by a stale process.

## Pitfalls
- A duplicate server masks every fix

## Examples
### Example 1:
**Input:** port=8000
**Expected:** JSON with ok

=== scripts/run.py ===
import json
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
print(json.dumps({"ok": True, "port": port}))
"""

_FAILING_BUNDLE = _LESSON_BUNDLE.replace(
    'print(json.dumps({"ok": True, "port": port}))',
    "sys.exit(1)",
)


def _lesson_page(
    *,
    kind: str = "procedure",
    generality: int = 4,
    helpful: int = 3,
    harmful: int = 0,
    status: str = "draft",
    page_id: str = "lp-1",
) -> SimpleNamespace:
    content = (
        "When a dev server ignores code changes, restart the stale process "
        "holding the port before editing code, because a duplicate server "
        "masks every fix.\n\n"
        f"**Kind:** {kind}\n"
        f"generality: {generality}\n\n"
        f"helpful: {helpful}\n"
        f"harmful: {harmful}"
    )
    return SimpleNamespace(
        id=page_id,
        title="Restart stale dev servers before debugging",
        content=content,
        tags=["lesson", kind, "server"],
        status=status,
    )


def _lesson_llm(bundle: str = _LESSON_BUNDLE):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=LLMResponse(content=bundle))
    return llm


class TestLessonQualification:
    def test_qualifying_lesson_accepted(self):
        pipeline = SkillMakerPipeline()
        assert pipeline._qualifies_for_promotion(_lesson_page()) is True

    def test_rejects_low_generality(self):
        pipeline = SkillMakerPipeline()
        assert pipeline._qualifies_for_promotion(_lesson_page(generality=3)) is False

    def test_rejects_negative_net_counters(self):
        pipeline = SkillMakerPipeline()
        assert pipeline._qualifies_for_promotion(_lesson_page(helpful=1, harmful=2)) is False

    def test_rejects_pitfall_kind(self):
        pipeline = SkillMakerPipeline()
        assert pipeline._qualifies_for_promotion(_lesson_page(kind="pitfall")) is False

    def test_rejects_archived(self):
        pipeline = SkillMakerPipeline()
        assert pipeline._qualifies_for_promotion(_lesson_page(status="archived")) is False

    @pytest.mark.asyncio
    async def test_detect_lesson_candidates_filters(self, mock_wiki):
        mock_wiki.list_pages = AsyncMock(
            return_value=[
                _lesson_page(page_id="ok"),
                _lesson_page(generality=2, page_id="low-gen"),
                _lesson_page(kind="tip", page_id="tip"),
                _lesson_page(status="archived", page_id="archived"),
            ]
        )
        pipeline = SkillMakerPipeline(wiki=mock_wiki)
        candidates = await pipeline.detect_lesson_candidates()
        assert [p.id for p in candidates] == ["ok"]


class TestGenerateSkillFromLesson:
    @pytest.mark.asyncio
    async def test_qualifying_lesson_produces_valid_script_backed_draft(self, tmp_path):
        pipeline = SkillMakerPipeline(
            config=SkillMakerConfig(),
            llm_client=_lesson_llm(),
            staging_dir=tmp_path / "staging",
        )
        proposal = await pipeline.generate_skill_from_lesson(_lesson_page())

        assert proposal is not None
        assert proposal.validation_result.is_valid
        assert proposal.validation_result.risks == []
        # Script-backed draft staged on disk
        staged = tmp_path / "staging" / proposal.skill_draft.id
        assert proposal.staged_dir == str(staged)
        assert (staged / "SKILL.md").exists()
        assert (staged / "scripts" / "run.py").exists()
        # The draft follows the v2 script-step pattern
        step = proposal.skill_draft.steps[0]
        assert step.script == "scripts/run.py"
        assert step.verification.json_has_keys == ["ok"]
        assert proposal.skill_draft.skill_dir == str(staged)
        assert proposal.pattern.tag == "lesson"

    @pytest.mark.asyncio
    async def test_smoke_run_failure_blocks_proposal(self, tmp_path):
        pipeline = SkillMakerPipeline(
            config=SkillMakerConfig(),
            llm_client=_lesson_llm(_FAILING_BUNDLE),
            staging_dir=tmp_path / "staging",
        )
        proposal = await pipeline.generate_skill_from_lesson(_lesson_page())
        assert proposal is None

    @pytest.mark.asyncio
    async def test_missing_script_bundle_returns_none(self, tmp_path):
        llm = MagicMock()
        llm.complete = AsyncMock(
            return_value=LLMResponse(content="+++\n... toml but no script block ...\n+++")
        )
        pipeline = SkillMakerPipeline(
            config=SkillMakerConfig(), llm_client=llm, staging_dir=tmp_path / "staging"
        )
        proposal = await pipeline.generate_skill_from_lesson(_lesson_page())
        assert proposal is None

    @pytest.mark.asyncio
    async def test_script_path_escape_rejected(self):
        pipeline = SkillMakerPipeline()
        raw = "+++\nx = 1\n+++\n\n=== scripts/../evil.py ===\nprint('x')\n"
        assert pipeline._split_skill_bundle(raw) is None

    @pytest.mark.asyncio
    async def test_no_llm_returns_none(self, tmp_path):
        pipeline = SkillMakerPipeline(staging_dir=tmp_path / "staging")
        assert await pipeline.generate_skill_from_lesson(_lesson_page()) is None


class TestLessonProposalInstallation:
    @pytest.mark.asyncio
    async def test_auto_install_uses_staged_dir(self, tmp_path, mock_installer):
        """Script-backed drafts install from the staged dir (scripts included)."""
        config = SkillMakerConfig(auto_install_approved=True)
        pipeline = SkillMakerPipeline(
            config=config,
            llm_client=_lesson_llm(),
            skill_installer=mock_installer,
            approval_gate=AutoApproveGate(),
            staging_dir=tmp_path / "staging",
        )
        proposal = await pipeline.generate_skill_from_lesson(_lesson_page())
        assert proposal is not None

        result = await pipeline.propose_installation(proposal)

        assert result is not None
        assert result.success is True
        mock_installer.install_from_path.assert_awaited_once()
        installed_from = mock_installer.install_from_path.call_args.args[0]
        assert str(installed_from) == proposal.staged_dir

    @pytest.mark.asyncio
    async def test_approved_message_points_at_staged_dir(self, tmp_path, mock_installer):
        pipeline = SkillMakerPipeline(
            config=SkillMakerConfig(),
            llm_client=_lesson_llm(),
            skill_installer=mock_installer,
            approval_gate=AutoApproveGate(),
            staging_dir=tmp_path / "staging",
        )
        proposal = await pipeline.generate_skill_from_lesson(_lesson_page())
        result = await pipeline.propose_installation(proposal)

        assert result is not None and result.success is True
        assert proposal.staged_dir in result.message
        mock_installer.install_from_path.assert_not_awaited()
