import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from vibe.adapters.openai import OpenAIAdapter
from vibe.core.config import LogConfig
from vibe.core.coordinators import ToolExecutor
from vibe.core.logger import SessionLogger
from vibe.core.model_gateway import LLMClient
from vibe.harness.constraints import HookPipeline
from vibe.tools.tool_system import Tool, ToolResult, ToolSystem


class DummyTool(Tool):
    def __init__(self, name="dummy", should_fail=False):
        super().__init__(name=name, description="Dummy tool for testing")
        self.should_fail = should_fail

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {"arg": {"type": "string"}}}

    async def execute(self, **kwargs) -> ToolResult:
        if self.should_fail:
            return ToolResult(success=False, content=None, error="Dummy tool failure")
        return ToolResult(success=True, content="Dummy tool success")


def test_session_logger_levels_and_rotation():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = LogConfig(
            enabled=True,
            log_dir=tmpdir,
            max_file_size_mb=1.0,
            retention_days=7,
        )
        logger = SessionLogger(config, session_id="test_session_123")
        logger.info("Informational message")
        logger.warning("Warning message with diagnostic detail")
        logger.error("Error message")
        logger.debug("Debug message")

        log_file = Path(tmpdir) / "session_test_session_123.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "[INFO]" in content and "Informational message" in content
        assert "[WARNING]" in content and "Warning message with diagnostic detail" in content
        assert "[ERROR]" in content and "Error message" in content
        assert "[DEBUG]" in content and "Debug message" in content


async def test_llm_client_logs_failures_and_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = LogConfig(enabled=True, log_dir=tmpdir)
        logger = SessionLogger(config, session_id="test_llm_log")

        mock_client = MagicMock(spec=httpx.AsyncClient)
        # Mock a 400 bad request error
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": {"message": "Invalid JSON parameter: unsupported field"}}'
        http_err = httpx.HTTPStatusError(
            "Client Error", request=MagicMock(), response=mock_response
        )
        mock_client.post = AsyncMock(side_effect=http_err)

        client = LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="sk-test",
            adapter=OpenAIAdapter(),
            client=mock_client,
            logger=logger,
            auto_fallback=True,
            fallback_chain=["gpt-4o", "gpt-4o-mini"],
        )

        res = await client.complete(messages=[{"role": "user", "content": "hello"}])
        assert res.is_error

        log_file = Path(tmpdir) / "session_test_llm_log.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "LLM Request Failed" in content
        assert "Invalid JSON parameter" in content


async def test_tool_executor_logs_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = LogConfig(enabled=True, log_dir=tmpdir)
        logger = SessionLogger(config, session_id="test_tool_log")

        tool_sys = ToolSystem()
        tool_sys.register_tool(DummyTool(name="succ_tool", should_fail=False))
        tool_sys.register_tool(DummyTool(name="fail_tool", should_fail=True))

        executor = ToolExecutor(
            tool_system=tool_sys,
            hook_pipeline=HookPipeline(),
            logger=logger,
        )

        results = await executor.execute(
            [
                {"function": {"name": "succ_tool", "arguments": {"arg": "val1"}}},
                {"function": {"name": "fail_tool", "arguments": {"arg": "val2"}}},
            ]
        )

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False

        log_file = Path(tmpdir) / "session_test_tool_log.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "Tool Execution Start: tool=succ_tool" in content
        assert "Tool Execution Success: tool=succ_tool" in content
        assert "Tool Execution Start: tool=fail_tool" in content
        assert "Tool Execution Failed: tool=fail_tool" in content
