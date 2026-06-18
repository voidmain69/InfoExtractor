"""Single gateway for all Ollama /api/chat interactions."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx


class OllamaGateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        max_concurrency: int = 0,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._model = model
        # Bound concurrent calls to protect a single shared model server.
        self._sem = asyncio.Semaphore(max_concurrency) if max_concurrency > 0 else None

    async def chat(self, system: str, user: str, *, timeout: float) -> str:
        """Send a system+user prompt, return the raw assistant message content."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        return await asyncio.wait_for(self._post(payload), timeout=timeout)

    async def chat_json(self, system: str, user: str, *, timeout: float) -> Any:
        """Like chat(), but strip markdown code fences and parse the body as JSON."""
        content = await self.chat(system, user, timeout=timeout)
        content = _strip_code_fences(content)
        return json.loads(content)

    async def _post(self, payload: dict) -> str:
        async with self._limit():
            resp = await self._client.post(f"{self._base_url}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()

    @asynccontextmanager
    async def _limit(self):
        if self._sem is None:
            yield
            return
        async with self._sem:
            yield


def _strip_code_fences(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return content.strip()
