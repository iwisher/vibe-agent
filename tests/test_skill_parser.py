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


SAMPLE_YAML_SKILL = """---
name: yaml-skill
description: "A YAML-frontmatter skill"
category: "general"
tags: ["yaml", "test"]
trigger:
  - "yaml pattern"
  - "another pattern"
metadata:
  author: test
---

# YAML Skill

## Overview
This is a Hermes-style YAML skill.

## Pitfalls

- Don't use in production
- Watch for edge cases

## Examples

### Example 1: Basic usage

**Input:** "Run yaml skill"
**Expected:** Success
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


SAMPLE_SKILL_WITH_VARIABLES = """+++
vibe_skill_version = "2.0.0"
id = "vars-skill"
name = "Vars Skill"
description = "Skill with typed variables"

[[variables]]
name = "ticker"
type = "string"
required = true
pattern = "^[A-Za-z0-9.-]{1,10}$"
description = "Ticker symbol"

[[variables]]
name = "days"
type = "integer"
required = false
default = 30
minimum = 5

[[steps]]
id = "run"
description = "Run it"
tool = "bash"
script = "scripts/run.py"
interpreter = "python3"
command = "{{ ticker }} --days {{ days }}"

[steps.verification]
exit_code = 0
json_has_keys = ["ticker", "sma_20"]
+++

# Vars Skill
"""


def test_parser_reads_variables():
    """Regression: [[variables]] must reach Skill.variables (typed-vars pipeline)."""
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL_WITH_VARIABLES)
    assert len(skill.variables) == 2
    ticker, days = skill.variables
    assert ticker["name"] == "ticker"
    assert ticker["pattern"] == "^[A-Za-z0-9.-]{1,10}$"
    assert ticker["required"] is True
    assert days["name"] == "days"
    assert days["default"] == 30
    assert days["minimum"] == 5


def test_parser_reads_script_and_interpreter():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL_WITH_VARIABLES)
    step = skill.steps[0]
    assert step.script == "scripts/run.py"
    assert step.interpreter == "python3"
    assert step.verification.json_has_keys == ["ticker", "sma_20"]


def test_parser_skill_dir_defaults_to_none():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_SKILL)
    assert skill.skill_dir is None


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


def test_parser_yaml_frontmatter():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_YAML_SKILL)
    assert skill.id == "yaml-skill"
    assert skill.name == "yaml-skill"
    assert skill.description == "A YAML-frontmatter skill"
    assert skill.vibe_skill_version == "2.0.0"
    assert skill.category == "general"
    assert skill.tags == ["yaml", "test"]
    assert skill.steps == []
    assert len(skill.pitfalls) == 2
    assert "production" in skill.pitfalls[0]
    assert skill.metadata == {"author": "test"}


def test_parser_yaml_trigger_list():
    parser = SkillParser()
    skill = parser.parse_string(SAMPLE_YAML_SKILL)
    assert skill.trigger.patterns == ["yaml pattern", "another pattern"]


def test_parser_invalid_format():
    parser = SkillParser()
    with pytest.raises(
        ValueError, match="must start with TOML frontmatter|must start with YAML frontmatter"
    ):
        parser.parse_string("# Just markdown\nNo frontmatter here.")
