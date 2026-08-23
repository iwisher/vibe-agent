"""Prompt templates for SkillMaker LLM skill generation."""

SKILL_GENERATION_PROMPT = """You are an expert at creating reusable automation skills for a \
CLI agent.

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


# Prompt for lesson→skill promotion (Workstream B4): compiles a validated,
# principle-level procedure lesson into a v2 *script-backed* skill following
# the stock-analysis pattern (typed variables, deterministic script step,
# verification). Placeholders are substituted via str.replace so the TOML
# examples can contain literal {{ variable }} templates without escaping.
LESSON_SKILL_GENERATION_PROMPT = """You are an expert at compiling reusable lessons into \
executable automation skills for a CLI agent.

A validated lesson from past sessions is being promoted into a script-backed \
skill. Deterministic logic must live in scripts/, not prose.

The output must follow this exact format — a SKILL.md file, then one delimiter \
line per bundled script:

+++
vibe_skill_version = "2.0.0"
id = "@SKILL_ID@"
name = "<short human-readable name>"
description = "<what the skill automates>"
category = "lesson"
tags = ["lesson"]

[[variables]]
name = "example_input"
type = "string"
required = false
default = "example"
description = "<what this input controls>"

[[steps]]
id = "run"
description = "Run the deterministic script and emit the result"
tool = "bash"
script = "scripts/run.py"
command = "{{ example_input }}"

[steps.verification]
exit_code = 0
+++

# <Skill Name>

## Overview
<one short paragraph>

## Pitfalls
- ...

## Examples
### Example 1:
**Input:** ...
**Expected:** ...

=== scripts/run.py ===
#!/usr/bin/env python3
<deterministic stdlib-only python script reading variables from argv>

Rules:
- Every typed variable MUST declare a default so the skill can be smoke-run \
with no arguments.
- The script reads variable values from argv (argparse or sys.argv) and prints \
its result; when the result is structured, print one JSON object and add \
json_has_keys to [steps.verification].
- Stdlib only: no network, no writes outside the working directory, no \
credentials, no subprocess.
- The skill must automate the procedure described by the lesson, generalized \
beyond the original task instance.

Lesson to compile:
Title: @LESSON_TITLE@

@LESSON_CONTENT@

Generate ONLY the SKILL.md content followed by the === scripts/... === \
block(s). No extra commentary."""


def build_lesson_skill_prompt(skill_id: str, lesson_title: str, lesson_content: str) -> str:
    """Build the LLM prompt for promoting a lesson page to a script-backed skill.

    Args:
        skill_id: Unique identifier the generated skill must use.
        lesson_title: Title of the qualifying lesson wiki page.
        lesson_content: Lesson body (counters stripped, length-bounded).

    Returns:
        Formatted prompt string ready for LLM completion.
    """
    return (
        LESSON_SKILL_GENERATION_PROMPT.replace("@SKILL_ID@", skill_id)
        .replace("@LESSON_TITLE@", lesson_title)
        .replace("@LESSON_CONTENT@", lesson_content)
    )


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
    """Build a structured LLM prompt for generating a SKILL.md draft.

    Args:
        skill_id: Unique identifier for the skill.
        skill_name: Human-readable name for the skill.
        description: Short description of what the skill does.
        category: Category tag for the skill.
        tags: List of tags associated with the skill.
        patterns: List of trigger patterns.
        required_tools: List of tools required by the skill.
        pattern_summary: Sanitized summary of the detected pattern.

    Returns:
        Formatted prompt string ready for LLM completion.
    """
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
