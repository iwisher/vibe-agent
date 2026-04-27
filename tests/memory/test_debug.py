import asyncio, json
from unittest.mock import AsyncMock, MagicMock
from vibe.memory.extraction import KnowledgeExtractor
from tests.memory.test_extraction import fake_llm, FakeLLMResponse, FakeMessage

async def debug():
    client = MagicMock()
    client.complete = AsyncMock(return_value=FakeLLMResponse(
        content=json.dumps([
            {
                "title": "Docker",
                "content": "Host",
                "tags": ["docker"],
                "citations": [{"session": "abc123", "message_index": 5}],
            }
        ])
    ))
    extractor = KnowledgeExtractor(client, MagicMock())
    messages = [FakeMessage(role="user", content="Hello")]
    try:
        items = await extractor.extract_from_session(messages, "sess")
        print("Success:", items)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(debug())
