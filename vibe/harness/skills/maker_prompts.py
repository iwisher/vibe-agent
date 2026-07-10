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
