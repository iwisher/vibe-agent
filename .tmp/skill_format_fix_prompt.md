# Task: Fix skill format support + Rich rendering crash

## Context
Two bugs were found in the skill install feature:

### Bug 1: Rich Panel crashes on dict content
`SkillInstallTool` and `SkillListTool` return `ToolResult(content=dict)` but `vibe/cli/main.py:184` passes `tr.content` directly to `Panel()` which only accepts strings. This causes:
```
NotRenderableError: Unable to render {'count': 0, 'skills': []}; 
A str, Segment or object with __rich_console__ method is required
```

### Bug 2: Parser only supports TOML frontmatter
`SkillParser.parse_string()` requires `+++` TOML frontmatter. Hermes skills use `---` YAML frontmatter. Need auto-detection.

## Files to Modify

### 1. vibe/tools/skill_install.py
- `SkillInstallTool._format_result()`: Change `content=dict` to `content=str` (formatted string)
- `SkillListTool.execute()`: Change `content=dict` to `content=str` (formatted string)
- Keep all the same information, just format as human-readable string

Example output format for SkillListTool:
```
Installed skills: 2
- cli-subagents (v2.0.0, installed 2026-05-20)
- hermes-agent (v2.1.0, installed 2026-05-21)
```

Example output format for SkillInstallTool:
```
Skill 'my-skill' installed successfully at ~/.vibe/skills/my-skill
Name: My Skill
Description: Does something useful
Version: 2.0.0
Category: general
Steps: 3
```

### 2. vibe/harness/skills/parser.py
- `parse_string()`: Auto-detect frontmatter format:
  - Starts with `+++` → parse as TOML (existing behavior)
  - Starts with `---` → parse as YAML, map to Skill model
  - Neither → raise ValueError with clear message

For YAML format, map fields:
- `name` → `id` (slugified: lowercase, replace spaces/special chars with hyphens)
- `name` → `name` (as-is)
- `description` → `description`
- `vibe_skill_version` → `"2.0.0"` (default for Hermes skills)
- `category` → from YAML or `"general"`
- `tags` → from YAML or `[]`
- `trigger` → from YAML `trigger` list (map to SkillTrigger.patterns)
- `steps` → `[]` (Hermes skills don't have executable steps)
- `pitfalls` → extract from `## Pitfalls` section in body
- `examples` → extract from `## Examples` section in body
- `metadata` → from YAML `metadata` or `{}`

Use `yaml.safe_load()` for YAML parsing. Add `import yaml` at top.

### 3. tests/harness/skills/test_parser.py
Add tests:
- `test_parse_yaml_frontmatter()`: Parse a YAML-frontmatter skill
- `test_parse_toml_frontmatter_still_works()`: Ensure TOML still works
- `test_parse_invalid_format()`: Test clear error for unsupported format

### 4. tests/tools/test_skill_install.py
Update assertions to expect string content instead of dict content.

## Constraints
- Follow existing code style
- Keep backward compatibility (TOML skills must still work)
- Run `pytest tests/harness/skills/test_parser.py -x -q` after parser changes
- Run `pytest tests/tools/test_skill_install.py -x -q` after tool changes
- Run `pytest tests/ -x -q` for full suite
- Do NOT modify vibe/cli/main.py (the fix is in the tools, not the CLI)
