"""Integration tests for LLMClient with native GeminiAdapter."""

from unittest.mock import AsyncMock, MagicMock

import httpx

from vibe.adapters.gemini import GeminiAdapter
from vibe.core.llm_types import LLMResponse
from vibe.core.model_gateway import LLMClient
from vibe.core.provider_registry import ProviderProfile, ProviderRegistry


async def test_llm_client_complete_with_gemini_adapter():
    gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Native Gemini response content"}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 12,
            "candidatesTokenCount": 8,
            "totalTokenCount": 20,
        },
    }

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_post_res = MagicMock(spec=httpx.Response)
    mock_post_res.json.return_value = gemini_resp
    mock_post_res.raise_for_status.return_value = None
    mock_client.post = AsyncMock(return_value=mock_post_res)

    adapter = GeminiAdapter()
    client = LLMClient(
        base_url="https://generativelanguage.googleapis.com",
        model="gemini-2.5-flash",
        api_key="AIzaSyTestSecret",
        adapter=adapter,
        client=mock_client,
    )

    response = await client.complete(
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
    )

    assert isinstance(response, LLMResponse)
    assert response.content == "Native Gemini response content"
    assert response.usage["total_tokens"] == 20

    # Verify posted payload format
    assert mock_client.post.call_count == 1
    call_args = mock_client.post.call_args
    url = call_args[0][0]
    kwargs = call_args[1]
    expected_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert url == expected_url
    assert kwargs["headers"]["x-goog-api-key"] == "AIzaSyTestSecret"
    assert kwargs["json"]["system_instruction"]["parts"][0]["text"] == (
        "You are a helpful assistant."
    )


async def test_provider_registry_wires_gemini_adapter():
    profile = ProviderProfile(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com",
        adapter_type="gemini",
        api_key="AIzaSyDirectKey",
        default_model="gemini-flash-latest",
    )
    registry = ProviderRegistry({"gemini": profile})
    client = registry.resolve_client("gemini")

    assert client.base_url == "https://generativelanguage.googleapis.com"
    assert client.model == "gemini-flash-latest"
    assert client.api_key == "AIzaSyDirectKey"
    assert isinstance(client.adapter, GeminiAdapter)
