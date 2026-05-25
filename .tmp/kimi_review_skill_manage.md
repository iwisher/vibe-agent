# Kimi Code Review: skill_manage wiring

## Diff to Review

```diff
diff --git a/vibe/core/query_loop_factory.py b/vibe/core/query_loop_factory.py
index 04efdf3..1fc55a6 100644
--- a/vibe/core/query_loop_factory.py
+++ b/vibe/core/query_loop_factory.py
@@ -9,6 +9,7 @@ from vibe.core.query_loop import QueryLoop
 from vibe.harness.constraints import HookPipeline
 from vibe.tools.bash import BashSandbox, BashTool
 from vibe.tools.file import ReadFileTool, WriteFileTool
+from vibe.tools.skill_manage import SkillManageTool
 from vibe.tools.tool_system import ToolSystem
 
 
@@ -112,6 +113,7 @@ class QueryLoopFactory:
         )
         tool_system.register_tool(ReadFileTool())
         tool_system.register_tool(WriteFileTool())
+        tool_system.register_tool(SkillManageTool())
         return tool_system
```

```python
# tests/core/test_query_loop_factory_skills.py
"""Tests for QueryLoopFactory skill tool wiring."""

from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.tools.skill_manage import SkillManageTool


def test_skill_manage_tool_is_registered():
    """Assert that SkillManageTool is registered in create_tool_system."""
    factory = QueryLoopFactory(
        base_url="http://localhost:11434",
        model="llama3.2",
    )
    tool_system = factory.create_tool_system()

    assert "skill_manage" in tool_system.list_tools()
    assert isinstance(tool_system._tools["skill_manage"], SkillManageTool)
```

## Context
- SkillManageTool already exists in vibe/tools/skill_manage.py (name="skill_manage", handles create/update actions)
- It was NOT previously wired into QueryLoopFactory
- This change adds it so the agent can create skills via chat
- skill_install and skill_list were already wired in a previous PR

## Questions
1. Is the import placement correct (alphabetical within the vibe.tools group)?
2. Should SkillManageTool be registered before or after SkillInstallTool/SkillListTool? (Currently only skill_manage is being added in this diff; skill_install/skill_list are already in the base)
3. Any security concerns with wiring skill_manage? It writes to ~/.vibe/skills/ with path traversal guards already in place.
4. Should the test also verify skill_install and skill_list are still registered (regression guard)?
5. Any naming confusion between skill_manage (create/update) vs skill_install (fetch from external)?
