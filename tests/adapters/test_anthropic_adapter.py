"""Tests for Anthropic-native adapter."""

import json

import pytest
from vibe.adapters.anthropic import AnthropicAdapter
from vibe.core.llm_types import LLMResponse


class TestAnthropicAdapter:
    def test_build_request_basic(self):
        adapter = AnthropicAdapter()
        url, headers, payload = adapter.build_request(
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet-20241022",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.7,
            max_tokens=1024,
        )
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["Content-Type"] == "application/json"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "x-api-key" not in headers
        assert payload["model"] == "claude-3-5-sonnet-20241022"
        assert payload["messages"] == [{"role": "user", "content": "hello"}]
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 1024
        assert "system" not in payload

    def test_build_request_with_api_key(self):
        adapter = AnthropicAdapter()
        url, headers, payload = adapter.build_request(
            base_url="https://api.kimi.com/coding",
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hello"}],
            api_key="sk-kimi-test",
        )
        assert headers["x-api-key"] == "sk-kimi-test"
        assert "Authorization" not in headers

    def test_build_request_extracts_system_messages(self):
        adapter = AnthropicAdapter()
        url, headers, payload = adapter.build_request(
            base_url="https://api.anthropic.com",
            model="claude-3-opus",
            messages=[
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "Write a sort function."},
            ],
        )
        assert payload["system"] == "You are a coding assistant."
        assert payload["messages"] == [{"role": "user", "content": "Write a sort function."}]

    def test_build_request_converts_tools(self):
        adapter = AnthropicAdapter()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ]
        url, headers, payload = adapter.build_request(
            base_url="https://api.anthropic.com",
            model="claude-3-opus",
            messages=[{"role": "user", "content": "read test.txt"}],
            tools=tools,
            tool_choice="required",
        )
        assert "tools" in payload
        anthropic_tool = payload["tools"][0]
        assert anthropic_tool["name"] == "read_file"
        assert anthropic_tool["input_schema"]["type"] == "object"

    def test_build_request_tool_choice_mapping(self):
        adapter = AnthropicAdapter()
        # "required" maps to "any"
        _, _, p1 = adapter.build_request(
            base_url="https://api.anthropic.com",
            model="claude-3-opus",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            tool_choice="required",
        )
        assert p1["tool_choice"] == {"type": "any"}

        # "none" maps to none (only meaningful when tools present)
        _, _, p2 = adapter.build_request(
            base_url="https://api.anthropic.com",
            model="claude-3-opus",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            tool_choice="none",
        )
        assert p2["tool_choice"] == {"type": "none"}

        # function-specific maps to tool name
        _, _, p3 = adapter.build_request(
            base_url="https://api.anthropic.com",
            model="claude-3-opus",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
            tool_choice={"type": "function", "function": {"name": "read_file"}},
        )
        assert p3["tool_choice"] == {"type": "tool", "name": "read_file"}

    def test_parse_response_basic(self):
        adapter = AnthropicAdapter()
        response_json = {
            "content": [{"type": "text", "text": "Hello there"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        result = adapter.parse_response(response_json)
        assert isinstance(result, LLMResponse)
        assert result.content == "Hello there"
        assert result.finish_reason == "end_turn"
        assert result.usage["prompt_tokens"] == 10
        assert result.usage["completion_tokens"] == 5
        assert result.usage["total_tokens"] == 15

    def test_parse_response_with_tool_use(self):
        adapter = AnthropicAdapter()
        response_json = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_01",
                    "name": "read_file",
                    "input": {"path": "/tmp/test.txt"},
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "stop_reason": "tool_use",
        }
        result = adapter.parse_response(response_json)
        assert result.content == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "tu_01"
        assert result.tool_calls[0]["type"] == "function"
        assert result.tool_calls[0]["function"]["name"] == "read_file"
        # Anthropic input dict must be serialized to JSON string to match OpenAI
        args = result.tool_calls[0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args) == {"path": "/tmp/test.txt"}

    def test_parse_response_multi_block(self):
        adapter = AnthropicAdapter()
        response_json = {
            "content": [
                {"type": "text", "text": "Here is the result:"},
                {"type": "tool_use", "id": "tu_02", "name": "calc", "input": {"a": 1, "b": 2}},
            ],
            "usage": {"input_tokens": 15, "output_tokens": 8},
        }
        result = adapter.parse_response(response_json)
        assert result.content == "Here is the result:"
        assert len(result.tool_calls) == 1

    def test_extract_system_messages(self):
        adapter = AnthropicAdapter()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "Always be concise."},
        ]
        system_content, remaining = adapter.extract_system_messages(messages)
        assert system_content == "You are helpful.\n\nAlways be concise."
        assert remaining == [{"role": "user", "content": "hello"}]

    def test_extract_system_messages_none(self):
        adapter = AnthropicAdapter()
        messages = [{"role": "user", "content": "hello"}]
        system_content, remaining = adapter.extract_system_messages(messages)
        assert system_content is None
        assert remaining == messages

    def test_health_check_endpoints(self):
        adapter = AnthropicAdapter()
        endpoints = adapter.health_check_endpoints(
            "https://api.anthropic.com", "claude-3-opus"
        )
        assert ("GET", "https://api.anthropic.com/v1/models") in endpoints
        assert ("POST", "https://api.anthropic.com/v1/messages") in endpoints
