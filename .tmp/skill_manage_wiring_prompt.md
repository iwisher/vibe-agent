# Task: Wire skill_manage tool into QueryLoopFactory

## Context
The `SkillManageTool` exists in `vibe/tools/skill_manage.py` but is NOT registered in `QueryLoopFactory.create_tool_system()`. We need to wire it so the agent can create/update skills via chat.

## Files to Modify

1. `vibe/core/query_loop_factory.py`
   - Add import: `from vibe.tools.skill_manage import SkillManageTool`
   - In `create_tool_system()`, add: `tool_system.register_tool(SkillManageTool())`

2. `tests/core/test_query_loop_factory_skills.py` (create if not exists)
   - Add assertions that `skill_manage` is in `tool_system.list_tools()`
   - Assert `tool_system._tools["skill_manage"]` is instance of `SkillManageTool`

## Constraints
- Follow existing code style in the file
- Keep imports grouped logically
- Tests must be minimal but verify wiring
- Run `pytest tests/core/test_query_loop_factory_skills.py -x -q` after changes
- Run `pytest tests/ -x -q` for full suite verification

## Do NOT modify
- `vibe/tools/skill_manage.py` (it already works standalone)
- Any other tool registrations
