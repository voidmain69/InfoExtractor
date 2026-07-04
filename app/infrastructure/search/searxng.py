from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time

import httpx
from cachetools import TTLCache

from app.core.config import settings
from app.domain.page import SearxNGResponse, SearxNGResult

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")


class _DiskCache:
    """Optional JSON-file layer under the in-memory search cache.

    Search responses are the scarcest resource in the pipeline: every repeated
    query costs upstream-engine goodwill, and a burst of identical queries
    (service restarts, repeated batch runs) is exactly what gets engines
    CAPTCHA-suspended. Persisting responses across restarts keeps that traffic
    at zero. Best-effort: any I/O problem degrades to memory-only."""

    def __init__(self, path: str, ttl: float):
        self._path = path
        self._ttl = ttl
        self._data: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            self._data = {
                k: v for k, v in raw.items()
                if isinstance(v, dict) and now - v.get("t", 0) < ttl
            }
            logger.info("search disk cache: loaded %d fresh entries", len(self._data))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("search disk cache unreadable (%s), starting empty", exc)

    def get(self, key: str) -> SearxNGResponse | None:
        entry = self._data.get(key)
        if not entry or time.time() - entry.get("t", 0) >= self._ttl:
            return None
        try:
            return SearxNGResponse.model_validate(entry["resp"])
        except Exception:
            return None

    async def set(self, key: str, resp: SearxNGResponse) -> None:
        self._data[key] = {"t": time.time(), "resp": resp.model_dump(mode="json")}
        async with self._lock:
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=os.path.dirname(self._path) or ".", suffix=".tmp"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False)
                os.replace(tmp, self._path)
            except Exception as exc:
                logger.debug("search disk cache write failed: %s", exc)


class _RateLimiter:
    """Bound concurrency and enforce a minimum spacing between outgoing calls.

    The upstream search engines CAPTCHA a single host when it bursts queries.
    One batch /attributes request can fan out into a dozen searches in a second;
    spacing them out keeps us under the engines' rate thresholds."""

    def __init__(self, min_interval: float, max_concurrency: int):
        self._min_interval = max(0.0, min_interval)
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def __aenter__(self):
        await self._sem.acquire()
        async with self._lock:
            loop = asyncio.get_event_loop()
            wait = self._min_interval - (loop.time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = loop.time()
        return self

    async def __aexit__(self, *exc):
        self._sem.release()


class SearxNGClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._cache: TTLCache = TTLCache(
            maxsize=settings.searxng_cache_max_size,
            ttl=settings.searxng_cache_ttl_seconds,
        )
        self._cache_lock = asyncio.Lock()
        self._limiter = _RateLimiter(
            settings.searxng_min_interval_seconds,
            settings.searxng_max_concurrency,
        )
        self._disk: _DiskCache | None = None
        if settings.searxng_cache_file:
            self._disk = _DiskCache(
                settings.searxng_cache_file, settings.searxng_cache_ttl_seconds
            )

    async def search(self, query: str, num_results: int = 10) -> SearxNGResponse:
        # Don't force English when the query carries Cyrillic — it would hide
        # local/UA retail and manufacturer pages.
        language = "all" if _CYRILLIC_RE.search(query) else "en"
        cache_key = (query, num_results, language)

        async with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        disk_key = f"{query}\x1f{num_results}\x1f{language}"
        if self._disk is not None:
            disk_hit = self._disk.get(disk_key)
            if disk_hit is not None:
                async with self._cache_lock:
                    self._cache[cache_key] = disk_hit
                return disk_hit

        response = await self._search_with_retry(query, num_results, language)

        # Only cache productive responses so a transient block doesn't get
        # pinned for the whole TTL.
        if response.results:
            async with self._cache_lock:
                self._cache[cache_key] = response
            if self._disk is not None:
                await self._disk.set(disk_key, response)
        return response

    async def _search_with_retry(
        self, query: str, num_results: int, language: str
    ) -> SearxNGResponse:
        attempts = max(1, settings.searxng_retry_attempts)
        backoff = settings.searxng_retry_backoff
        last = SearxNGResponse()
        for attempt in range(attempts):
            async with self._limiter:
                last = await self._search_once(query, num_results, language)
            if last.results:
                return last
            if attempt < attempts - 1:
                logger.debug(
                    "searxng empty for %r, retry %d/%d", query, attempt + 1, attempts
                )
                await asyncio.sleep(backoff * (2 ** attempt))
        return last

    async def _search_once(
        self, query: str, num_results: int, language: str
    ) -> SearxNGResponse:
        params = {
            "q": query,
            "format": "json",
            "language": language,
            "safesearch": str(settings.searxng_safesearch),
            "pageno": "1",
        }
        try:
            resp = await self._client.get(f"{self._base_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("searxng request failed for %r: %s", query, exc)
            return SearxNGResponse()

        # Surface upstream engine blocks in logs so throttling is diagnosable.
        unresponsive = data.get("unresponsive_engines") or []
        if unresponsive and not data.get("results"):
            logger.warning("searxng unresponsive engines for %r: %s", query, unresponsive)

        raw_results = data.get("results", []) or []
        results = []
        for r in raw_results[:num_results]:
            url = r.get("url", "")
            if not url:
                continue
            results.append(
                SearxNGResult(
                    url=url,
                    title=r.get("title", ""),
                    content=r.get("content"),
                    score=r.get("score"),
                )
            )

        raw_answers = data.get("answers", []) or []
        answers = list(dict.fromkeys(str(a) for a in raw_answers if a))

        infoboxes = data.get("infoboxes", []) or []

        return SearxNGResponse(results=results, infoboxes=infoboxes, answers=answers)
