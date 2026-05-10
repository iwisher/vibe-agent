import tempfile
from pathlib import Path

from vibe.core.coordinators import ToolExecutor
from vibe.preferences.tool_prefs import ToolPreferenceRegistry
from vibe.preferences.registry import PreferenceRegistry
from vibe.tools.tool_system import ToolResult, ToolSystem


class MockToolSystem:
    def __init__(self):
        self.last_call = None

    async def execute_tool(self, name, **args):
        self.last_call = {"name": name, "args": args}
        return ToolResult(success=True, content="ok")


class TestToolPreferenceIntegration:
    async def test_tool_executor_applies_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            prefs = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            prefs.set_default_args("bash", {"timeout": 30})

            mock_tools = MockToolSystem()
            from vibe.harness.constraints import HookPipeline

            executor = ToolExecutor(
                tool_system=mock_tools,
                hook_pipeline=HookPipeline(),
                tool_prefs=prefs,
            )

            tool_call = {
                "id": "call_1",
                "function": {
                    "name": "bash",
                    "arguments": {"command": "echo hi"},
                },
            }

            results = await executor.execute([tool_call])

            assert results[0].success is True
            assert mock_tools.last_call["args"]["timeout"] == 30
            assert mock_tools.last_call["args"]["command"] == "echo hi"
