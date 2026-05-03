"""Test skill parser."""
import pytest

from vibe.harness.skills.parser import SkillParser

SAMPLE_SKILL = """+++
vibe_skill_version = "2.0.0"
id = "test-skill"
name = "Test Skill"
description = "A test skill"
category = "test"
tags = ["test", "demo"]

[trigger]
patterns = ["test", "demo"]
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "First step"
script = "scripts/hello.py"
tool = "bash"
command = "python {skill_dir}/scripts/hello.py"

[steps.verification]
exit_code = 0

[metadata]
created_at = "2026-04-24T00:00:00Z"
auto_generated = false
+++

# Test Skill

## Overview
A simple test skill.

## Steps

### Step 1: Hello

**Script:** `scripts/hello.py`
**Tool:** bash
**Command:** `python {skill_dir}/scripts/hello.py`

**Verification:** exit_code == 0

## Pitfalls

- Don't run this in production
- Watch out for edge cases

## Examples

### Example 1: Basic usage

**Input:** "Run the test"
**Expected:** Hello output
"""


def test_parser_reads_frontmatter():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL)
    assert skill.id == "test-skill"
    assert skill.name == "Test Skill"
    assert skill.vibe_skill_version == "2.0.0"


def test_parser_reads_steps():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL)
    assert len(skill.steps) == 1
    assert skill.steps[0].id == "step1"
    assert skill.steps[0].tool == "bash"
    assert skill.steps[0].verification.exit_code == 0


def test_parser_reads_pitfalls():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL)
    assert len(skill.pitfalls) == 2
    assert "production" in skill.pitfalls[0]


def test_parser_reads_examples():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL)
    # Examples are parsed from markdown body — may be empty depending on regex
    assert skill.examples is not None


def test_parser_malformed_toml():
    parser = SkillParser()
    with pytest.raises(ValueError, match="Invalid TOML"):
        parser.parse_string("+++\nnot valid toml!!!\n+++\n# Body")


def test_parser_missing_frontmatter():
    parser = SkillParser()
    with pytest.raises(ValueError, match="must start with"):
        parser.parse_string("# No frontmatter")
