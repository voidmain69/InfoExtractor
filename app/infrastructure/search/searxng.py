from __future__ import annotations

import re

import httpx

from app.domain.page import SearxNGResponse, SearxNGResult

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")


class SearxNGClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def search(self, query: str, num_results: int = 10) -> SearxNGResponse:
        # Don't force English when the query carries Cyrillic — it would hide
        # local/UA retail and manufacturer pages.
        language = "all" if _CYRILLIC_RE.search(query) else "en"
        params = {
            "q": query,
            "format": "json",
            "language": language,
            "safesearch": "0",
            "pageno": "1",
        }
        try:
            resp = await self._client.get(f"{self._base_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return SearxNGResponse()

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
