"""Tests for SkillMakerPipeline."""

from dataclasses import dataclass
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
