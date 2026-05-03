import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from tests.memory.test_extraction import FakeLLMResponse, FakeMessage
from vibe.memory.extraction import KnowledgeExtractor


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
    except Exception:
        import traceback
        traceback.print_exc()

asyncio.run(debug())
