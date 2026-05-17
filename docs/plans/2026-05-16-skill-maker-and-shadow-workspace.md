# Phase 4.2 + 5.2 Unified Plan: Skill-Maker + Shadow Workspace Integration

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Implement the two remaining open items on the vibe-agent roadmap:
1. **Phase 4.2 — Autonomous Skill Generation (Skill-Maker):** Detect recurring task patterns from wiki extractions, generate SKILL.md drafts via LLM, sandbox-validate them, and propose installation via the approval gate.
2. **Phase 5.2 — Shadow Workspace Rollbacks:** Auto-create shadow branches before write-heavy tool operations, auto-offer rollback on ERROR/INCOMPLETE states.

**Architecture:**
- **Skill-Maker** is a background pipeline (`SkillMakerPipeline`) that periodically scans the wiki for recurring patterns, uses the LLM to draft skills, validates them through the existing `SkillValidator`, and proposes installation via `SkillInstaller` with an approval gate. All default-disabled, opt-in via config.
- **Shadow Workspace** extends the existing `ShadowBranchManager` with QueryLoop integration: auto-create shadows before write-heavy `ToolExecutor` calls, and auto-trigger rollback offer when the loop enters ERROR/INCOMPLETE. Also adds comprehensive test coverage (currently zero tests exist).

**Tech Stack:** Python 3.11+, Pydantic v2, existing skill system (parser/validator/installer), existing wiki system (LLMWiki/PageIndex), existing git shadow tools.

**Test Baseline:** 1382 tests collected, ~1382 passing (as of 2026-05-16). Target: +80-100 new tests, zero regressions.

---

## Current State Analysis

### Already Implemented

| Component | Status | Location |
|-----------|--------|----------|
| ShadowBranchManager (create/list/restore/clean) | ✅ Done | `vibe/tools/git_shadow.py` |
| Shadow CLI commands (list/create/restore/clean/rollback) | ✅ Done | `vibe/cli/main.py` |
| Skill system v2 (parser/validator/installer/executor) | ✅ Done | `vibe/harness/skills/` |
| Approval gate protocol (CLI/AutoApprove/AutoReject) | ✅ Done | `vibe/harness/skills/approval.py` |
| Wiki system (LLMWiki/PageIndex/TelemetryCollector) | ✅ Done | `vibe/memory/` |
| QueryLoop state machine + checkpointing | ✅ Done | `vibe/core/query_loop.py` |
| ToolExecutor with tool_prefs | ✅ Done | `vibe/core/coordinators.py` |

### Still Missing / Needs Completion

| Workstream | Missing Pieces |
|------------|---------------|
| **4.2 Skill-Maker** | Entire pipeline — no files exist. Needs: pattern detector, LLM skill drafter, sandbox validator, proposal flow |
| **5.2 Shadow Tests** | Zero tests for `git_shadow.py`. Needs: unit tests for all manager methods, integration tests for CLI |
| **5.2 QueryLoop Integration** | Shadow auto-create not wired into ToolExecutor. Shadow rollback not triggered on ERROR/INCOMPLETE |

---

## Phase Execution Order

```
Phase A: Skill-Maker Core Pipeline (4.2)
    → Gemini CLI review → fix → PASS
Phase B: Shadow Workspace Tests + QueryLoop Integration (5.2)
    → Gemini CLI review → fix → PASS
Phase C: Integration tests + full suite regression
    → Gemini CLI bulk review → fix → PASS
```

**Parallelization opportunity:** While coding Phase B, run Gemini review for Phase A in background.

---

# ─────────────────────────────────────────
# PHASE A: Skill-Maker Pipeline (4.2)
# ─────────────────────────────────────────

## Overview

The `SkillMakerPipeline` detects recurring task patterns from the wiki, generates SKILL.md drafts using the LLM, validates them through the existing security pipeline, and proposes installation. It runs as a background task (like wiki extraction and RLM analysis) and is entirely opt-in.

**Key design decisions:**
- Pattern detection uses wiki page tags + title similarity (not LLM — cheap and deterministic)
- Skill generation uses the LLM with a structured prompt that outputs TOML-frontmatter SKILL.md
- Validation reuses existing `SkillParser` + `SkillValidator` — no new security logic
- Installation reuses existing `SkillInstaller` with approval gate — no new install logic
- All config-gated: `skill_maker.enabled` (default False), `skill_maker.min_pattern_frequency` (default 3)

## Files

| File | Action |
|------|--------|
| `vibe/harness/skills/maker.py` | **NEW** — SkillMakerPipeline core |
| `vibe/harness/skills/maker_prompts.py` | **NEW** — LLM prompt templates for skill generation |
| `vibe/harness/skills/maker_config.py` | **NEW** — Pydantic config model |
| `tests/harness/skills/test_maker.py` | **NEW** — Unit tests for pattern detection, generation, validation |
| `vibe/core/query_loop.py` | Modify — wire skill_maker background task |
| `vibe/core/query_loop_factory.py` | Modify — inject SkillMakerPipeline |

## Task A1: Create SkillMakerConfig

**Objective:** Define the Pydantic config model for the skill maker.

**Files:** Create `vibe/harness/skills/maker_config.py`

```python
"""Configuration for the SkillMakerPipeline."""
from pydantic import BaseModel, Field


class SkillMakerConfig(BaseModel):
    """Configuration for autonomous skill generation."""

    enabled: bool = False
    min_pattern_frequency: int = Field(default=3, ge=2, description="Minimum wiki pages with same tag to trigger pattern detection")
    max_skills_per_session: int = Field(default=1, ge=0, description="Max skill proposals per session")
    sandbox_timeout_seconds: int = Field(default=30, ge=5, description="Timeout for sandbox validation")
    auto_install_approved: bool = Field(default=False, description="Auto-install skills that pass validation with no risks")
    llm_model: str = Field(default="", description="Model to use for skill generation (empty = use loop's model)")
    excluded_tags: list[str] = Field(default_factory=lambda: ["session", "telemetry", "system"], description="Tags to exclude from pattern detection")
```

**Step 1:** Write the file.
**Step 2:** Verify import: `python -c "from vibe.harness.skills.maker_config import SkillMakerConfig; c = SkillMakerConfig()"`

## Task A1b: Wire SkillMakerConfig into VibeConfig

**Objective:** Add skill_maker config to the root application config.

**Files:** Modify `vibe/core/config.py`

Add to `VibeConfig` (or the root config class):
```python
from vibe.harness.skills.maker_config import SkillMakerConfig

class VibeConfig(BaseModel):
    # ... existing fields ...
    skill_maker: SkillMakerConfig = Field(default_factory=SkillMakerConfig)
```

Also update `docs/sample_config.yaml`:
```yaml
skill_maker:
  enabled: false
  min_pattern_frequency: 3
  max_skills_per_session: 1
  sandbox_timeout_seconds: 30
  auto_install_approved: false
  llm_model: ""
  excluded_tags:
    - session
    - telemetry
    - system
```

**Step 1:** Modify config.py.
**Step 2:** Update sample_config.yaml.
**Step 3:** Verify: `python -c "from vibe.core.config import VibeConfig; c = VibeConfig(); print(c.skill_maker.enabled)"`

## Task A2: Create Maker Prompt Templates

**Objective:** Define the LLM prompt that generates valid SKILL.md TOML frontmatter.

**Files:** Create `vibe/harness/skills/maker_prompts.py`

```python
"""Prompt templates for SkillMaker LLM skill generation."""

SKILL_GENERATION_PROMPT = """You are an expert at creating reusable automation skills for a CLI agent.

Given a recurring task pattern observed across multiple sessions, create a SKILL.md file
with TOML frontmatter (+++) and a markdown body.

The skill must follow this exact format:

+++
vibe_skill_version = "1.0"
id = "{skill_id}"
name = "{skill_name}"
description = "{description}"
category = "{category}"
tags = {tags_json}

[[steps]]
id = "step_1"
description = "..."
tool = "bash"
command = "..."

[[steps]]
id = "step_2"
description = "..."
tool = "file_write"
command = "..."

[trigger]
patterns = {patterns_json}
required_tools = {tools_json}
+++

## Pitfalls
- ...

## Examples
### Example 1:
**Input:** ...
**Expected:** ...

Task pattern summary:
{pattern_summary}

Generate ONLY the SKILL.md content. No extra commentary."""


def build_skill_generation_prompt(
    skill_id: str,
    skill_name: str,
    description: str,
    category: str,
    tags: list[str],
    patterns: list[str],
    required_tools: list[str],
    pattern_summary: str,
) -> str:
    import json

    return SKILL_GENERATION_PROMPT.format(
        skill_id=skill_id,
        skill_name=skill_name,
        description=description,
        category=category,
        tags_json=json.dumps(tags),
        patterns_json=json.dumps(patterns),
        tools_json=json.dumps(required_tools),
        pattern_summary=pattern_summary,
    )
```

**Step 1:** Write the file.
**Step 2:** Verify: `python -c "from vibe.harness.skills.maker_prompts import build_skill_generation_prompt; print(build_skill_generation_prompt('test', 'Test', 'desc', 'general', ['a'], ['p'], ['bash'], 'summary')[:100])"`

## Task A3: Create SkillMakerPipeline Core

**Objective:** Implement the full pipeline: detect → generate → validate → propose.

**Files:** Create `vibe/harness/skills/maker.py`

Key components:

```python
"""Autonomous skill generation pipeline (Phase 4.2)."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

    def detect_patterns(self) -> list[DetectedPattern]:
        """Scan wiki for recurring patterns based on tags and titles."""
        if self.wiki is None:
            return []

        # Collect all pages and group by tag
        tag_to_pages: dict[str, list[dict]] = {}
        try:
            pages = self.wiki.list_pages()
        except Exception as e:
            logger.warning("SkillMaker: wiki.list_pages() failed: %s", e)
            return []

        for page in pages:
            for tag in page.tags:
                if tag in self.config.excluded_tags:
                    continue
                tag_to_pages.setdefault(tag, []).append({
                    "title": page.title,
                    "id": page.id,
                    "content": page.content[:500],  # truncated for analysis
                })

        patterns = []
        for tag, pages in tag_to_pages.items():
            if len(pages) < self.config.min_pattern_frequency:
                continue

            # Extract suggested tools from page content
            suggested_tools = self._extract_tools_from_pages(pages)

            # Confidence: frequency + distinct page ratio
            confidence = min(1.0, len(pages) / self.config.min_pattern_frequency * 0.3 + 0.4)

            patterns.append(DetectedPattern(
                pattern_id=f"pattern_{tag}_{uuid.uuid4().hex[:8]}",
                tag=tag,
                page_titles=[p["title"] for p in pages],
                page_ids=[p["id"] for p in pages],
                frequency=len(pages),
                suggested_tools=suggested_tools,
                confidence=confidence,
            ))

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
        description = f"Automatically generated skill for {pattern.tag} tasks. Detected across {pattern.frequency} wiki pages."
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
            safe = safe.replace("+++", "").replace("\"", "'")
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
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                skill_dir = Path(tmp) / proposal.skill_draft.id
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(proposal.raw_markdown, encoding="utf-8")
                result = await self.installer.install_from_path(skill_dir)
                return result

        logger.info("SkillMaker: proposal %s approved but not auto-installed", proposal.proposal_id)
        return InstallResult(
            success=True,
            message=f"Skill '{proposal.skill_draft.id}' approved. Install with: vibe skill install --path <dir>",
            skill_id=proposal.skill_draft.id,
        )

    async def run_once(self) -> list[SkillProposal]:
        """Run one detection + generation cycle. Returns proposals created."""
        if not self.config.enabled:
            return []

        patterns = self.detect_patterns()
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
```

**Step 1:** Write the file.
**Step 2:** Verify import: `python -c "from vibe.harness.skills.maker import SkillMakerPipeline; p = SkillMakerPipeline()"`

## Task A4: Wire SkillMaker into QueryLoop

**Objective:** Add skill_maker as an optional background task in QueryLoop, triggered after successful sessions.

**Files:** Modify `vibe/core/query_loop.py`

Changes needed:
1. Add `skill_maker: Any | None = None` to `__init__` params
2. Store `self.skill_maker = skill_maker`
3. In `run()`, after session completes (COMPLETED state), trigger skill maker:

```python
        # After session completion, trigger skill maker in background
        # Store task reference so we can await it during teardown if needed
        self._skill_maker_task: asyncio.Task | None = None
        if self.skill_maker is not None and self._state == QueryState.COMPLETED:
            try:
                self._skill_maker_task = asyncio.create_task(self.skill_maker.run_once())
            except Exception:
                pass  # Non-critical background task

        # In QueryLoop cleanup / close method, await background tasks:
        # if self._skill_maker_task and not self._skill_maker_task.done():
        #     try:
        #         await asyncio.wait_for(self._skill_maker_task, timeout=5.0)
        #     except Exception:
        #         pass
```

4. Add `skill_maker` param to `QueryLoopFactory._create_query_loop()` in `vibe/core/query_loop_factory.py`

**Step 1:** Modify QueryLoop.__init__.
**Step 2:** Modify QueryLoop.run() completion path.
**Step 3:** Modify QueryLoopFactory.
**Step 4:** Verify syntax: `python -c "import vibe.core.query_loop"`

## Task A5: Write Skill-Maker Tests

**Objective:** Comprehensive test coverage for pattern detection, skill generation, and proposal flow.

**Files:** Create `tests/harness/skills/test_maker.py`

Test cases:
1. `test_detect_patterns_empty_wiki` — empty wiki returns no patterns
2. `test_detect_patterns_single_tag` — tag with enough pages returns pattern
3. `test_detect_patterns_excluded_tags` — excluded tags are filtered
4. `test_detect_patterns_insufficient_frequency` — below threshold returns empty
5. `test_extract_tools_from_pages` — heuristic tool extraction
6. `test_generate_skill_no_llm` — returns None when no LLM
7. `test_generate_skill_max_per_session` — respects max_skills_per_session
8. `test_propose_installation_rejected` — AutoRejectGate blocks risky skills
9. `test_propose_installation_auto_install` — auto_install_approved works
10. `test_run_once_disabled` — disabled config returns empty
11. `test_build_pattern_summary` — summary formatting
12. `test_reset_session` — counters reset

Use mocks for wiki, llm_client, and installer.

**Step 1:** Write tests.
**Step 2:** Run: `pytest tests/harness/skills/test_maker.py -v`
**Step 3:** All tests should pass.

## Task A6: Gemini CLI Review for Phase A

**Prompt:**
```
Context: This is a code review for Phase A of vibe-agent Skill-Maker implementation.
Phase A included: SkillMakerConfig, maker prompts, SkillMakerPipeline (detect/generate/validate/propose), QueryLoop wiring, and tests.

Files to review:
- vibe/harness/skills/maker_config.py — Pydantic config
- vibe/harness/skills/maker_prompts.py — LLM prompt templates
- vibe/harness/skills/maker.py — Core pipeline
- vibe/core/query_loop.py — Wiring changes
- vibe/core/query_loop_factory.py — Factory wiring
- tests/harness/skills/test_maker.py — Tests

Key design decisions:
- Pattern detection is heuristic (tag frequency + tool extraction), not LLM-based — keeps it cheap
- Skill generation uses the loop's LLM client with structured prompt
- Validation reuses existing SkillValidator — no new security logic
- Installation reuses existing SkillInstaller with approval gate
- All default-disabled (config.enabled=False)

Review criteria:
1. Code quality: Python idioms, type hints, docstrings, error handling
2. Architecture: Consistency with existing codebase, abstraction level
3. Bugs & edge cases: Race conditions, resource leaks, silent failures
4. Security: Prompt injection risks, skill validation bypass
5. API consistency: Do new methods follow existing patterns?

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES
## WARNINGS
## NITS

Do NOT modify any files. Only read and critique.
```

---

# ─────────────────────────────────────────
# PHASE B: Shadow Workspace Tests + QueryLoop Integration (5.2)
# ─────────────────────────────────────────

## Overview

The `ShadowBranchManager` already exists and works. What's missing:
1. **Zero test coverage** — no tests exist for any shadow functionality
2. **No QueryLoop integration** — shadows are not auto-created before write-heavy operations, and rollback is not offered on ERROR/INCOMPLETE

This phase adds tests and wires shadows into the tool execution flow.

## Files

| File | Action |
|------|--------|
| `tests/tools/test_git_shadow.py` | **NEW** — Unit tests for ShadowBranchManager |
| `tests/tools/test_git_shadow_integration.py` | **NEW** — Integration tests with real git repos |
| `vibe/core/coordinators.py` | Modify — wire shadow creation into ToolExecutor |
| `vibe/core/query_loop.py` | Modify — trigger rollback offer on ERROR/INCOMPLETE |
| `vibe/cli/main.py` | Modify — add `vibe rollback` top-level command (alias) |

## Task B1: Write Shadow Unit Tests

**Objective:** Test all ShadowBranchManager methods without real git (mocked).

**Files:** Create `tests/tools/test_git_shadow.py`

Test cases:
1. `test_sanitize_session_id` — special chars replaced, safe chars preserved
2. `test_create_shadow_no_git` — returns None when git unavailable
3. `test_create_shadow_success` — creates branch, stores metadata
4. `test_list_shadows_empty` — no shadows returns empty list
5. `test_list_shadows_with_metadata` — parses git config metadata
6. `test_restore_shadow_not_found` — returns False for missing shadow
7. `test_restore_shadow_success` — checkout + reset works
8. `test_clean_shadows_old` — removes old shadows
9. `test_clean_shadows_none_old` — keeps recent shadows
10. `test_is_write_heavy_bash_destructive` — detects rm -rf
11. `test_is_write_heavy_bash_safe` — safe bash commands not flagged
12. `test_is_write_heavy_file_write` — file_write flagged
13. `test_is_write_heavy_read_only` — read ops not flagged
14. `test_noop_manager` — NoOpShadowManager returns safe defaults

Mock `subprocess.run` for unit tests.

**Step 1:** Write tests.
**Step 2:** Run: `pytest tests/tools/test_git_shadow.py -v`

## Task B2: Write Shadow Integration Tests

**Objective:** Test with real git repositories in temp dirs.

**Files:** Create `tests/tools/test_git_shadow_integration.py`

Test cases:
1. `test_create_shadow_real_repo` — create shadow in real git repo
2. `test_restore_shadow_preserves_state` — restore brings back original files
3. `test_list_shadows_real_repo` — list finds created shadows
4. `test_clean_shadows_real_repo` — clean removes old, keeps new
5. `test_shadow_with_uncommitted_changes` — stash create captures changes

Use `tempfile.TemporaryDirectory` + `git init` for each test. Pass `cwd=` to subprocess instead of `os.chdir()`.

**Step 1:** Write tests.
**Step 2:** Run: `pytest tests/tools/test_git_shadow_integration.py -v`

## Task B3: Wire Shadow into ToolExecutor

**Objective:** Auto-create shadow before write-heavy tool operations.

**Files:** Modify `vibe/core/coordinators.py` (ToolExecutor)

Changes:
1. Add `shadow_manager: Any | None = None` to ToolExecutor.__init__
2. In `execute_tool()` or the tool execution path, before executing write-heavy tools:

```python
        # Auto-create shadow before write-heavy operations
        if self.shadow_manager is not None and hasattr(self.shadow_manager, 'is_write_heavy_operation'):
            if self.shadow_manager.is_write_heavy_operation(tool_name, arguments):
                # Track by session ID to avoid duplicates across sessions
                if not hasattr(self, '_shadows_created'):
                    self._shadows_created: set[str] = set()
                session_key = session_id or "default"
                if session_key not in self._shadows_created:
                    try:
                        self.shadow_manager.create_shadow(session_key)
                        self._shadows_created.add(session_key)
                    except Exception:
                        pass  # Non-critical — don't block tool execution
```

3. Store `_shadow_created` flag per session.

**Step 1:** Read ToolExecutor to understand current structure.
**Step 2:** Add shadow_manager param and shadow creation logic.
**Step 3:** Verify syntax.

## Task B4: Wire Shadow Rollback into QueryLoop

**Objective:** On ERROR/INCOMPLETE state, offer rollback via status message.

**Files:** Modify `vibe/core/query_loop.py`

Changes:
1. Add `shadow_manager: Any | None = None` to QueryLoop.__init__
2. Store `self.shadow_manager = shadow_manager`
3. In the ERROR/INCOMPLETE handling path, after yielding the error result:

```python
        # Offer rollback if shadow exists
        if self.shadow_manager is not None and self._session_id:
            try:
                shadows = self.shadow_manager.list_shadows()
                matching = [s for s in shadows if s.session_id == self._session_id]
                if matching:
                    yield QueryResult(
                        is_status=True,
                        status_message=f"Shadow backup available. Run `vibe shadow restore {self._session_id}` to rollback.",
                        state=self._state,
                    )
            except Exception as e:
                if self.logger:
                    self.logger.debug("Failed to list shadows for rollback offer: %s", e)
```

**Step 1:** Modify QueryLoop.__init__.
**Step 2:** Modify error/incomplete paths in run().
**Step 3:** Verify syntax.

## Task B5: Add `vibe rollback` Top-Level Command

**Objective:** Add a convenient top-level `vibe rollback` command.

**Files:** Modify `vibe/cli/main.py`

Add near existing shadow commands:

```python
@app.command("rollback")
def rollback_command(
    session_id: str | None = typer.Argument(None, help="Session ID to rollback (default: latest)"),
):
    """Rollback workspace to the latest shadow backup."""
    shadow_rollback(session_id)
```

**Step 1:** Add command.
**Step 2:** Verify CLI loads: `python -c "from vibe.cli.main import app"`

## Task B6: Gemini CLI Review for Phase B

**Prompt:**
```
Context: This is a code review for Phase B of vibe-agent Shadow Workspace integration.
Phase B included: shadow unit tests, integration tests, ToolExecutor wiring, QueryLoop rollback offer, and CLI alias.

Files to review:
- tests/tools/test_git_shadow.py — Unit tests
- tests/tools/test_git_shadow_integration.py — Integration tests
- vibe/core/coordinators.py — ToolExecutor shadow wiring
- vibe/core/query_loop.py — Rollback offer wiring
- vibe/cli/main.py — CLI alias

Key design decisions:
- Shadow creation is best-effort (exceptions swallowed) — never blocks tool execution
- Rollback is offered via status message, not auto-executed — user must explicitly restore
- One shadow per session max — flag prevents duplicate creation
- Tests use mocked subprocess for unit, real git for integration

Review criteria:
1. Code quality: Python idioms, type hints, docstrings, error handling
2. Test quality: Coverage, isolation, no os.chdir(), proper cleanup
3. Security: subprocess safety, path sanitization
4. Integration: Does wiring match existing patterns?

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES
## WARNINGS
## NITS

Do NOT modify any files. Only read and critique.
```

---

# ─────────────────────────────────────────
# PHASE C: Integration & Regression
# ─────────────────────────────────────────

## Task C1: Full Test Suite Regression

**Command:** `pytest tests/ -q`
**Expected:** 1382+ passing, 0 failures (or only pre-existing failures)

## Task C2: Run New Test Files

**Commands:**
```bash
pytest tests/harness/skills/test_maker.py -v
pytest tests/tools/test_git_shadow.py -v
pytest tests/tools/test_git_shadow_integration.py -v
```

## Task C3: Lint and Format Check

**Commands:**
```bash
ruff check vibe/ tests/
ruff format --check vibe/ tests/
```

## Task C4: Bulk Gemini CLI Review

**Prompt:**
```
Context: This is a bulk code review for Phase 4.2 (Skill-Maker) + Phase 5.2 (Shadow Workspace) of vibe-agent.

Phases implemented:
- Phase A: SkillMakerPipeline with pattern detection, LLM generation, validation, and proposal
- Phase B: Shadow workspace tests + QueryLoop integration

Files changed:
- vibe/harness/skills/maker_config.py (+20 lines) — config
- vibe/harness/skills/maker_prompts.py (+40 lines) — prompts
- vibe/harness/skills/maker.py (+250 lines) — core pipeline
- vibe/core/query_loop.py (+30/-5 lines) — wiring
- vibe/core/query_loop_factory.py (+10 lines) — factory wiring
- vibe/core/coordinators.py (+25 lines) — ToolExecutor shadow wiring
- vibe/cli/main.py (+10 lines) — rollback alias
- tests/harness/skills/test_maker.py (+300 lines) — maker tests
- tests/tools/test_git_shadow.py (+250 lines) — shadow unit tests
- tests/tools/test_git_shadow_integration.py (+150 lines) — shadow integration tests

Review criteria:
1. Code quality: Python idioms, type hints, docstrings, error handling
2. Architecture: Consistency with existing codebase
3. Bugs & edge cases: Race conditions, resource leaks, silent failures
4. Security: Prompt injection, path traversal, subprocess safety
5. Test coverage: Adequate for both workstreams
6. Integration: Do new components fit cleanly into existing flow?

Deliverable format:
## OVERALL_VERDICT: (pass / needs_minor_fixes / needs_major_revisions)
## CRITICAL ISSUES
## WARNINGS
## NITS
```

## Task C5: Update ROADMAP and CHANGELOG

**Files:** Modify `docs/ROADMAP.md`, `docs/CHANGELOG.md`

- Mark 4.2 and 5.2 as COMPLETED
- Update test count
- Update version to v0.3.5-alpha

## Task C6: Final Commit

```bash
git add -A
git commit -m "feat: Phase 4.2 Skill-Maker + Phase 5.2 Shadow Workspace Integration

- SkillMakerPipeline: detect recurring patterns from wiki, generate SKILL.md
  via LLM, validate through existing security pipeline, propose installation
- Shadow workspace: auto-create before write-heavy ops, auto-offer rollback
  on ERROR/INCOMPLETE, comprehensive test coverage
- 80+ new tests, zero regressions"
```

---

# Verification Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Full suite | `pytest tests/ -q` | 1460+ passing |
| Maker tests | `pytest tests/harness/skills/test_maker.py -v` | 12 passed |
| Shadow unit | `pytest tests/tools/test_git_shadow.py -v` | 14 passed |
| Shadow integration | `pytest tests/tools/test_git_shadow_integration.py -v` | 5 passed |
| Lint | `ruff check vibe/ tests/` | Clean |
| Format | `ruff format --check vibe/ tests/` | Clean |
| Import maker | `python -c "from vibe.harness.skills.maker import SkillMakerPipeline"` | OK |
| Import shadow | `python -c "from vibe.tools.git_shadow import ShadowBranchManager"` | OK |

---

# Rollback Plan

| Workstream | How to Disable |
|------------|---------------|
| Skill-Maker | Set `skill_maker.enabled=false` in config, or don't pass `skill_maker` to QueryLoop |
| Shadow auto-create | Don't pass `shadow_manager` to ToolExecutor |
| Shadow rollback offer | Don't pass `shadow_manager` to QueryLoop |

Both features are entirely opt-in and have zero behavioral impact when not configured.

---

*Plan written: 2026-05-16*
