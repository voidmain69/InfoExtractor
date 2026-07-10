"""Single gateway for all Ollama /api/chat interactions."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Circuit breaker: after this many consecutive connection failures the gateway
# fast-fails every call for the cooldown period instead of waiting out the full
# connect timeout each time. An unreachable Ollama host (VPN down, box off)
# otherwise stalls every request by the whole timeout, serially, per LLM call —
# the service must degrade to its deterministic fallbacks quickly.
_BREAKER_FAILURES = 2
_BREAKER_COOLDOWN = 120.0
_CONNECT_TIMEOUT = 3.0

# Deterministic decoding: greedy (temperature 0) + a fixed seed so the same
# (model, prompt) yields byte-identical output across runs. Ollama's default is
# temperature 0.8 with a random seed — the root of П6 (the same spec text
# resolved to 10 vs 8 attributes between runs), which makes the acquisition
# benchmark noisy and un-A/B-able. top_p tightened to match catalog-service's
# own ollama-client. (Phase-3 М1b — determinism.)
_GEN_OPTIONS = {"temperature": 0, "top_p": 0.1, "seed": 42}


class OllamaUnavailable(Exception):
    """Raised immediately while the circuit breaker is open."""


class OllamaGateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        max_concurrency: int = 0,
        keep_alive: str | None = None,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._keep_alive = keep_alive
        # Bound concurrent calls to protect a single shared model server.
        self._sem = asyncio.Semaphore(max_concurrency) if max_concurrency > 0 else None
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    async def chat(self, system: str, user: str, *, timeout: float) -> str:
        """Send a system+user prompt, return the raw assistant message content."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": _GEN_OPTIONS,
            **({"keep_alive": self._keep_alive} if self._keep_alive else {}),
        }
        return await asyncio.wait_for(self._post(payload, timeout), timeout=timeout)

    async def chat_json(self, system: str, user: str, *, timeout: float) -> Any:
        """Like chat(), but strip markdown code fences and parse the body as JSON."""
        content = await self.chat(system, user, timeout=timeout)
        content = _strip_code_fences(content)
        return json.loads(content)

    async def warmup(self, timeout: float = 300.0) -> None:
        """Pre-load the model with a trivial call so per-request stage timeouts
        (6–60s) never have to absorb a multi-minute cold load; combined with
        keep_alive the model then stays resident. Never raises."""
        try:
            await self.chat("You are a warmup probe.", "Reply with: ok", timeout=timeout)
            logger.info("Ollama model %s warmed up", self._model)
        except Exception as exc:
            logger.warning("Ollama warmup failed: %s", exc)

    @property
    def available(self) -> bool:
        """False while the circuit breaker is open (recent connect failures)."""
        return asyncio.get_event_loop().time() >= self._breaker_open_until

    def _check_breaker(self) -> None:
        if not self.available:
            raise OllamaUnavailable(
                f"Ollama at {self._base_url} unreachable; breaker open"
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_connect_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_FAILURES:
            self._breaker_open_until = (
                asyncio.get_event_loop().time() + _BREAKER_COOLDOWN
            )
            logger.warning(
                "Ollama at %s unreachable %d times; fast-failing LLM calls for %.0fs",
                self._base_url, self._consecutive_failures, _BREAKER_COOLDOWN,
            )

    async def _post(self, payload: dict, timeout: float) -> str:
        self._check_breaker()
        # Short connect timeout: an unreachable host must fail in seconds, not
        # hang for the full generation timeout (generation itself may be slow,
        # so read/write keep the caller's generous budget).
        req_timeout = httpx.Timeout(
            timeout, connect=min(_CONNECT_TIMEOUT, timeout)
        )
        async with self._limit():
            try:
                resp = await self._client.post(
                    f"{self._base_url}/api/chat", json=payload, timeout=req_timeout
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                self._record_connect_failure()
                raise OllamaUnavailable(str(exc)) from exc
        resp.raise_for_status()
        self._record_success()
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
