# Plan: Interactive Skill Install Tool for Vibe Chat

## Goal
Enable users to install vibe skills through interactive chat ("install skill from https://github.com/...") instead of requiring the `vibe skill install` CLI command. The agent should be able to fetch, validate, and install skills via tool calls during a session.

## Current State
- `vibe skill install <source>` exists as a CLI command
- `SkillInstaller` class exists in `vibe/harness/skills/installer.py` with methods: `install_from_git`, `install_from_tarball`, `install_from_path`
- `QueryLoopFactory.create_tool_system()` registers: `BashTool`, `ReadFileTool`, `WriteFileTool`
- No skill management tools are exposed to the agent during chat sessions

## Changes Required

### 1. New File: `vibe/tools/skill_install.py`
**Purpose:** Tool implementations for skill_install and skill_list

**SkillInstallTool:**
- Inherits from `Tool` base class
- Name: `skill_install`
- Schema params: `source` (required string), `skill_id` (optional string)
- Routes to appropriate installer method based on source type:
  - `.tar.gz`/`.tgz` → `install_from_tarball`
  - `http` or `.git` → `install_from_git`
  - else → `install_from_path`
- Uses `ChatApprovalGate` (auto-approve warnings, block risks) for chat context
- Returns rich ToolResult with skill metadata (name, description, version, category, tags, steps_count, variables)

**SkillListTool:**
- Inherits from `Tool` base class
- Name: `skill_list`
- No params
- Returns list of installed skills with id, version, installed_at, path

**ChatApprovalGate:**
- Implements `ApprovalGate` protocol
- Auto-rejects if critical risks present
- Auto-approves warnings (LLM already decided to install based on user request)

### 2. Modify: `vibe/core/query_loop_factory.py`
**Purpose:** Wire new tools into the tool system

- Import `SkillInstallTool`, `SkillListTool` from `vibe.tools.skill_install`
- Register both tools in `create_tool_system()` after `WriteFileTool`

### 3. New File: `tests/tools/test_skill_install.py`
**Purpose:** Unit tests for the new tools

**Test cases:**
- `test_skill_install_tool_schema` — verify schema structure
- `test_skill_install_tool_missing_source` — error on missing required param
- `test_skill_install_tool_local_path_success` — install from local directory (mocked)
- `test_skill_install_tool_local_path_not_found` — error on non-existent path
- `test_skill_list_tool_empty` — empty skills directory
- `test_skill_list_tool_with_skills` — list returns installed skills
- `test_chat_approval_gate_blocks_risks` — risks = blocked
- `test_chat_approval_gate_approves_warnings` — warnings = approved
- `test_format_result_includes_metadata` — verify rich output on success

### 4. New File: `tests/core/test_query_loop_factory_skills.py`
**Purpose:** Integration test for factory wiring

**Test cases:**
- `test_create_tool_system_includes_skill_tools` — verify tools are registered

## Files to Create
1. `vibe/tools/skill_install.py` (~180 lines)
2. `tests/tools/test_skill_install.py` (~120 lines)
3. `tests/core/test_query_loop_factory_skills.py` (~30 lines)

## Files to Modify
1. `vibe/core/query_loop_factory.py` — add 2 imports + 2 register_tool calls

## Verification
- `pytest tests/tools/test_skill_install.py -x -q`
- `pytest tests/core/test_query_loop_factory_skills.py -x -q`
- `pytest tests/ -x -q` (full suite, ensure no regressions)

## Design Decisions
1. **ChatApprovalGate auto-approves warnings:** In chat context, the user already asked to install. We only block critical security risks.
2. **Reuses existing SkillInstaller:** No duplication of clone/validate/install logic.
3. **Rich metadata output:** Post-install parsing of SKILL.md gives the agent (and user) useful context about what was installed.
4. **No uninstall tool (yet):** Scope is install+list only. Uninstall can be added later if needed.
