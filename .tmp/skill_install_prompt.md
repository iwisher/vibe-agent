# AGY Implementation Prompt: Interactive Skill Install Tool

## Context
You are implementing a feature for vibe-agent (an open agent harness platform). The project source is at `/Users/rsong/DevSpace/vibe-agent/`.

## Task
Implement the interactive skill install tool feature per the plan at:
`/Users/rsong/DevSpace/vibe-agent/.hermes/plans/skill-install-tool-plan.md`

## Key Files to Read First
1. `vibe/tools/tool_system.py` — base Tool class
2. `vibe/harness/skills/installer.py` — SkillInstaller (reuse this)
3. `vibe/harness/skills/approval.py` — ApprovalGate protocol
4. `vibe/harness/skills/parser.py` — SkillParser
5. `vibe/core/query_loop_factory.py` — where to wire tools

## Coding Rules (MUST FOLLOW)
1. Think Before Coding — read all relevant files first
2. Simplicity First — minimal changes, reuse existing code
3. Surgical Changes — only touch what's needed
4. Read Before You Write — understand existing patterns
5. Tests Verify Intent Not Just Behavior
6. Match Codebase Conventions — follow existing style
7. Fail Loud — clear error messages

## Implementation Details

### 1. Create `vibe/tools/skill_install.py`
Implement:
- `ChatApprovalGate` — auto-reject risks, auto-approve warnings
- `SkillInstallTool` — install from git/tarball/local path
  - `get_schema()` returns OpenAI-style function schema
  - `execute(**kwargs)` routes to appropriate installer
  - `_format_result()` enriches output with skill metadata
- `SkillListTool` — list installed skills

### 2. Modify `vibe/core/query_loop_factory.py`
- Add imports for `SkillInstallTool`, `SkillListTool`
- Register both in `create_tool_system()`

### 3. Create `tests/tools/test_skill_install.py`
Test:
- Schema validation
- Missing source error
- Local path install (use tmpdir with a valid SKILL.md)
- Path not found error
- Skill list empty and populated
- ChatApprovalGate behavior
- Rich result formatting

### 4. Create `tests/core/test_query_loop_factory_skills.py`
Test that factory registers the new tools.

## Important Notes
- The base `Tool.execute()` signature is `async def execute(self, **kwargs)` — do NOT add positional params
- Use `pytest-asyncio` for async tests (follow existing test patterns)
- Create temporary SKILL.md files in tests using the TOML frontmatter format (see existing tests for examples)
- Do NOT modify the CLI commands (`vibe/cli/skill_commands.py`) — this is purely about adding tools to the agent's tool system
- Run `pytest tests/tools/test_skill_install.py -x -q` when done
- Run `pytest tests/core/test_query_loop_factory_skills.py -x -q` when done
