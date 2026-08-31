"""Tests for Google Gemini native API adapter."""

import json

from vibe.adapters.gemini import GeminiAdapter
from vibe.core.llm_types import LLMResponse


class TestGeminiAdapter:
    def test_build_request_basic(self):
        adapter = GeminiAdapter()
        url, headers, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "Hello Gemini"}],
            temperature=0.4,
            max_tokens=2048,
            api_key="AIzaSyTestKey123",
        )
        assert (
            url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        )
        assert headers["Content-Type"] == "application/json"
        assert headers["x-goog-api-key"] == "AIzaSyTestKey123"
        assert payload["generationConfig"]["temperature"] == 0.4
        assert payload["generationConfig"]["maxOutputTokens"] == 2048
        assert payload["contents"] == [{"role": "user", "parts": [{"text": "Hello Gemini"}]}]
        assert "system_instruction" not in payload

    def test_build_request_strips_model_prefix(self):
        adapter = GeminiAdapter()
        url, _, _ = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="models/gemini-flash-latest",
            messages=[{"role": "user", "content": "test"}],
        )
        assert (
            url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
        )

    def test_build_request_extracts_system_instruction(self):
        adapter = GeminiAdapter()
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": "Implement quicksort."},
            ],
        )
        assert payload["system_instruction"] == {
            "parts": [{"text": "You are a helpful coding assistant."}]
        }
        assert payload["contents"] == [
            {"role": "user", "parts": [{"text": "Implement quicksort."}]}
        ]

    def test_build_request_converts_tools(self):
        adapter = GeminiAdapter()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Run shell command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            }
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "list files"}],
            tools=tools,
            tool_choice="auto",
        )
        assert "tools" in payload
        assert len(payload["tools"]) == 1
        assert "function_declarations" in payload["tools"][0]
        decl = payload["tools"][0]["function_declarations"][0]
        assert decl["name"] == "bash"
        assert decl["description"] == "Run shell command"
        assert decl["parameters"]["properties"]["command"]["type"] == "string"
        assert payload["tool_config"] == {"function_calling_config": {"mode": "AUTO"}}

    def test_build_request_tool_choice_variants(self):
        adapter = GeminiAdapter()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        # mode required -> ANY
        _, _, payload_req = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "read"}],
            tools=tools,
            tool_choice="required",
        )
        assert payload_req["tool_config"] == {"function_calling_config": {"mode": "ANY"}}

        # mode none -> NONE
        _, _, payload_none = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "read"}],
            tools=tools,
            tool_choice="none",
        )
        assert payload_none["tool_config"] == {"function_calling_config": {"mode": "NONE"}}

        # function-specific dict
        _, _, payload_specific = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "read"}],
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "read_file"}},
        )
        assert payload_specific["tool_config"] == {
            "function_calling_config": {
                "mode": "ANY",
                "allowed_function_names": ["read_file"],
            }
        }

    def test_build_request_multi_turn_with_tool_calls_and_responses(self):
        adapter = GeminiAdapter()
        messages = [
            {"role": "user", "content": "Check status"},
            {
                "role": "assistant",
                "content": "Checking status now.",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "check_status",
                            "arguments": '{"service": "auth"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "name": "check_status",
                "content": '{"status": "ok", "latency": 12}',
            },
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=messages,
        )
        contents = payload["contents"]
        assert len(contents) == 3
        # Turn 1: user
        assert contents[0] == {"role": "user", "parts": [{"text": "Check status"}]}
        # Turn 2: model with text + functionCall
        assert contents[1]["role"] == "model"
        assert contents[1]["parts"][0] == {"text": "Checking status now."}
        assert contents[1]["parts"][1] == {
            "functionCall": {"name": "check_status", "args": {"service": "auth"}}
        }
        # Turn 3: tool result as functionResponse
        assert contents[2]["role"] == "user"
        assert contents[2]["parts"][0] == {
            "functionResponse": {
                "name": "check_status",
                "response": {
                    "name": "check_status",
                    "content": '{"status": "ok", "latency": 12}',
                },
            }
        }

    def test_tool_message_name_recovered_from_tool_call_id(self):
        """Regression: tool results without a name must recover the function
        name from Gemini-style tool_call_id (call_{idx}_{name}) so
        functionResponse.name matches its functionCall (API 400 otherwise)."""
        adapter = GeminiAdapter()
        messages = [
            {"role": "user", "content": "Check status"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_0_check_status",
                        "type": "function",
                        "function": {"name": "check_status", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_0_check_status",
                "content": "ok",
            },
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=messages,
        )
        contents = payload["contents"]
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "check_status"

    def test_build_stream_request(self):
        adapter = GeminiAdapter()
        url, headers, payload = adapter.build_stream_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "stream test"}],
            api_key="AIzaSyTestKey123",
        )
        assert (
            url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
        )
        assert headers["x-goog-api-key"] == "AIzaSyTestKey123"

    def test_parse_response_text_and_usage(self):
        adapter = GeminiAdapter()
        response_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello world from Gemini!"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 6,
                "totalTokenCount": 16,
            },
        }
        resp = adapter.parse_response(response_json)
        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello world from Gemini!"
        assert resp.finish_reason == "stop"
        assert resp.tool_calls is None
        assert resp.usage == {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}

    def test_parse_response_with_tool_calls(self):
        adapter = GeminiAdapter()
        response_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "I will execute the command."},
                            {
                                "functionCall": {
                                    "name": "bash",
                                    "args": {"command": "ls -la"},
                                }
                            },
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 25,
                "candidatesTokenCount": 15,
                "totalTokenCount": 40,
            },
        }
        resp = adapter.parse_response(response_json)
        assert resp.content == "I will execute the command."
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc["function"]["name"] == "bash"
        assert json.loads(tc["function"]["arguments"]) == {"command": "ls -la"}

    def test_parse_stream_chunk(self):
        adapter = GeminiAdapter()
        chunk_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "streaming part "}],
                        "role": "model",
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 3,
                "totalTokenCount": 8,
            },
        }
        resp = adapter.parse_stream_chunk(chunk_json)
        assert resp is not None
        assert resp.content == "streaming part "
        assert resp.usage == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}

    def test_health_check_endpoints_and_response(self):
        adapter = GeminiAdapter()
        endpoints = adapter.health_check_endpoints(
            base_url="https://generativelanguage.googleapis.com",
            model_id="gemini-2.5-flash",
        )
        assert len(endpoints) == 1
        method, url = endpoints[0]
        assert method == "GET"
        assert url == "https://generativelanguage.googleapis.com/v1beta/models"

        assert (
            adapter.parse_health_response(
                "GET", url, {"models": [{"name": "models/gemini-2.5-flash"}]}
            )
            is True
        )
        assert adapter.parse_health_response("GET", url, {"models": []}) is False

    def test_thought_signature_parsed_and_preserved_in_request(self):
        adapter = GeminiAdapter()
        # Parse stream chunk with thoughtSignature
        chunk_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "bash",
                                    "args": {"command": "ls"},
                                },
                                "thoughtSignature": "ErYCCrMCARFNMg...",
                            }
                        ],
                        "role": "model",
                    }
                }
            ]
        }
        res = adapter.parse_stream_chunk(chunk_json)
        assert res is not None
        assert res.tool_calls is not None
        assert res.tool_calls[0]["thought_signature"] == "ErYCCrMCARFNMg..."

        # Now build next turn request with this tool call
        messages = [
            {"role": "user", "content": "list directory"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": res.tool_calls,
            },
            {
                "role": "tool",
                "tool_call_id": res.tool_calls[0]["id"],
                "name": "bash",
                "content": "file1.txt\nfile2.txt",
            },
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-flash-latest",
            messages=messages,
        )
        contents = payload["contents"]
        assert len(contents) == 3
        # Model turn should have functionCall and thoughtSignature
        assert contents[1]["role"] == "model"
        assert contents[1]["parts"][0]["functionCall"]["name"] == "bash"
        assert contents[1]["parts"][0]["thoughtSignature"] == "ErYCCrMCARFNMg..."
        # User turn should have functionResponse
        assert contents[2]["role"] == "user"
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "bash"

    def test_compacted_history_starting_with_assistant_has_user_turn(self):
        """Compacted history starting with an assistant message must prepend a user turn

        so Gemini API does not reject with 400 'First content should be with role user'.
        """
        adapter = GeminiAdapter()
        messages = [
            {"role": "system", "content": "[Context summarized: 2 earlier messages omitted]"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_0_bash",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_0_bash",
                "name": "bash",
                "content": "output.txt",
            },
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-flash-latest",
            messages=messages,
        )
        contents = payload["contents"]
        assert len(contents) == 3
        # Turn 0 must be user
        assert contents[0]["role"] == "user"
        assert "text" in contents[0]["parts"][0]
        # Turn 1 is model with functionCall
        assert contents[1]["role"] == "model"
        assert contents[1]["parts"][0]["functionCall"]["name"] == "bash"
        # Turn 2 is user with functionResponse
        assert contents[2]["role"] == "user"
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "bash"

    def test_orphaned_tool_message_converted_to_text(self):
        """Tool responses without preceding model functionCall must be converted to text."""
        adapter = GeminiAdapter()
        messages = [
            {"role": "tool", "name": "browser", "content": "<html>page</html>"},
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-flash-latest",
            messages=messages,
        )
        contents = payload["contents"]
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert "Tool Output (browser)" in contents[0]["parts"][0]["text"]

    def test_consecutive_same_role_messages_merged(self):
        """Consecutive user or model messages must be merged for strict alternation."""
        adapter = GeminiAdapter()
        messages = [
            {"role": "user", "content": "Part 1"},
            {"role": "user", "content": "Part 2"},
            {"role": "assistant", "content": "Ans 1"},
            {"role": "assistant", "content": "Ans 2"},
        ]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-flash-latest",
            messages=messages,
        )
        contents = payload["contents"]
        assert len(contents) == 2
        assert contents[0]["role"] == "user"
        assert len(contents[0]["parts"]) == 2
        assert contents[1]["role"] == "model"
        assert len(contents[1]["parts"]) == 2

    def test_empty_or_system_only_messages_produces_valid_user_turn(self):
        """Only system messages must still produce a valid non-empty user turn."""
        adapter = GeminiAdapter()
        messages = [{"role": "system", "content": "You are helpful."}]
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-flash-latest",
            messages=messages,
        )
        contents = payload["contents"]
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert "text" in contents[0]["parts"][0]

    def test_parse_response_with_thinking(self):
        """Gemini thought parts must be extracted into reasoning_content."""
        adapter = GeminiAdapter()
        response_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Let me think through this step by step...", "thought": True},
                            {"text": "Here is the final answer."},
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 15,
                "candidatesTokenCount": 20,
                "totalTokenCount": 35,
            },
        }
        resp = adapter.parse_response(response_json)
        assert resp.content == "Here is the final answer."
        assert resp.reasoning_content == "Let me think through this step by step..."

    def test_parse_stream_chunk_with_thinking(self):
        """Streaming thought chunk must yield reasoning_content."""
        adapter = GeminiAdapter()
        thought_chunk = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Thinking token...", "thought": True},
                        ],
                    },
                }
            ],
        }
        resp = adapter.parse_stream_chunk(thought_chunk)
        assert resp is not None
        assert resp.content == ""
        assert resp.reasoning_content == "Thinking token..."

        content_chunk = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Output token", "thought": False},
                        ],
                    },
                }
            ],
        }
        resp2 = adapter.parse_stream_chunk(content_chunk)
        assert resp2 is not None
        assert resp2.content == "Output token"
        assert resp2.reasoning_content == ""

    def test_build_request_includes_thinking_config(self):
        """Thinking-capable Gemini models must include thinkingConfig in generationConfig."""
        adapter = GeminiAdapter()
        _, _, payload = adapter.build_request(
            base_url="https://generativelanguage.googleapis.com",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert "thinkingConfig" in payload["generationConfig"]
        assert payload["generationConfig"]["thinkingConfig"] == {"includeThoughts": True}
