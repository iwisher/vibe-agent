"""Regression tests for QueryLoop._build_llm_messages tool-name propagation.

Tool-result messages must carry the function name so provider adapters
(Gemini functionResponse.name) can match them to their functionCall.
"""

from unittest.mock import AsyncMock

from vibe.core.model_gateway import LLMClient
from vibe.core.query_loop import Message, QueryLoop
from vibe.tools.tool_system import ToolSystem


def _loop():
    llm = AsyncMock(spec=LLMClient)
    llm.model = "test-model"
    return QueryLoop(llm_client=llm, tool_system=ToolSystem())


def test_tool_messages_carry_function_name():
    loop = _loop()
    loop.messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_0_bash",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        ),
        Message(
            role="tool",
            content="done",
            tool_call_id="call_0_bash",
            metadata={"tool_name": "bash"},
        ),
    ]

    msgs = loop._build_llm_messages()

    assert msgs[2]["name"] == "bash"
    assert msgs[2]["tool_call_id"] == "call_0_bash"


def test_tool_messages_without_metadata_omit_name():
    loop = _loop()
    loop.messages = [Message(role="tool", content="done", tool_call_id="x")]

    msgs = loop._build_llm_messages()

    assert "name" not in msgs[0]
