"""Tests for OpenAI-compatible adapter."""

import pytest
from vibe.adapters.openai import OpenAIAdapter
from vibe.core.llm_types import LLMResponse


class TestOpenAIAdapter:
    def test_build_request_basic(self):
        adapter = OpenAIAdapter()
        url, headers, payload = adapter.build_request(
            base_url="http://localhost:11434",
            model="llama3.2",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.7,
        )
        assert url == "http://localhost:11434/v1/chat/completions"
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers
        assert payload["model"] == "llama3.2"
        assert payload["messages"] == [{"role": "user", "content": "hello"}]
        assert payload["temperature"] == 0.7
        assert "max_tokens" not in payload

    def test_build_request_with_api_key(self):
        adapter = OpenAIAdapter()
        url, headers, payload = adapter.build_request(
            base_url="http://api.example.com",
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            api_key="sk-test123",
        )
        assert headers["Authorization"] == "Bearer sk-test123"

    def test_build_request_with_tools(self):
        adapter = OpenAIAdapter()
        tools = [
            {
                "type": "function",
                "function": {"name": "read_file", "description": "Read a file"},
            }
        ]
        url, headers, payload = adapter.build_request(
            base_url="http://api.example.com",
            model="gpt-4",
            messages=[{"role": "user", "content": "read test.txt"}],
            temperature=0.5,
            max_tokens=100,
            tools=tools,
            tool_choice="auto",
        )
        assert payload["max_tokens"] == 100
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"

    def test_parse_response_basic(self):
        adapter = OpenAIAdapter()
        response_json = {
            "choices": [
                {
                    "message": {"content": "Hello there"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        result = adapter.parse_response(response_json)
        assert isinstance(result, LLMResponse)
        assert result.content == "Hello there"
        assert result.finish_reason == "stop"
        assert result.usage["total_tokens"] == 15
        assert result.tool_calls is None

    def test_parse_response_with_tool_calls(self):
        adapter = OpenAIAdapter()
        response_json = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "/tmp/test.txt"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        result = adapter.parse_response(response_json)
        assert result.content == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "call_123"

    def test_parse_response_empty_choices(self):
        """Empty choices list should not crash (Gemini review finding)."""
        adapter = OpenAIAdapter()
        response_json = {
            "choices": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        result = adapter.parse_response(response_json)
        assert result.content == ""
        assert result.finish_reason is None

    def test_extract_system_messages_noop(self):
        adapter = OpenAIAdapter()
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hello"},
        ]
        system_content, remaining = adapter.extract_system_messages(messages)
        assert system_content is None
        assert remaining == messages

    def test_health_check_endpoints(self):
        adapter = OpenAIAdapter()
        endpoints = adapter.health_check_endpoints("http://localhost:11434", "llama3.2")
        assert ("GET", "http://localhost:11434/v1/models") in endpoints
        assert ("POST", "http://localhost:11434/v1/chat/completions") in endpoints

    def test_parse_health_response_models_endpoint(self):
        adapter = OpenAIAdapter()
        assert adapter.parse_health_response("GET", "/v1/models", {"data": [{"id": "llama3.2"}]}) is True
        assert adapter.parse_health_response("GET", "/v1/models", {"data": []}) is False
