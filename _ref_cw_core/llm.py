"""LLM client for local model at http://127.0.0.1:8000."""

import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from pydantic import BaseModel


class Message(BaseModel):
    """Chat message."""
    role: str
    content: str


class LLMClient:
    """Client for local LLM API."""
    
    def __init__(
        self,
        base_url: str = "http://ai-api.applesay.cn",
        model: str = "qwen3.5-plus",
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.getenv("LLM_API_KEY", "sk-WAEUwVx1GmT3C2CREbBc2fD53fEf4dB6A373773d28CfAfA6")
        self.client = httpx.AsyncClient(timeout=300.0)
    
    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> str:
        """Send chat completion request."""
        payload = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        response = await self.client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        
        data = response.json()
        return data["choices"][0]["message"]["content"]
    
    async def stream_chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion."""
        payload = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        async with self.client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        if content := chunk["choices"][0]["delta"].get("content"):
                            yield content
                    except (json.JSONDecodeError, KeyError):
                        continue
    
    async def complete(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Simple completion with single prompt."""
        messages = [Message(role="user", content=prompt)]
        return await self.chat(messages, temperature, max_tokens)
    
    async def structured_output(
        self,
        messages: List[Message],
        output_schema: Dict[str, Any],
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """Get structured JSON output."""
        system_msg = (
            "You must respond with valid JSON matching this schema:\n"
            f"{json.dumps(output_schema, indent=2)}\n\n"
            "Respond ONLY with the JSON, no markdown formatting."
        )
        messages = [Message(role="system", content=system_msg)] + messages
        
        response = await self.chat(messages, temperature, max_tokens=4000)
        
        # Clean up response (remove markdown code blocks if present)
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        return json.loads(response)
