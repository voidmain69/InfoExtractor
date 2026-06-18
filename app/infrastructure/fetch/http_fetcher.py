import asyncio
import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.domain.page import FetchedPage

logger = logging.getLogger(__name__)

_REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
_WS_RE = re.compile(r"\s{2,}")
_HEADERS = {
    "User-Agent": settings.user_agent,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


async def fetch_pages(
    urls: list[str],
    titles: dict[str, str],
) -> list[FetchedPage]:
    sem = asyncio.Semaphore(settings.max_concurrent_fetches)
    async with httpx.AsyncClient(
        timeout=settings.page_fetch_timeout_seconds,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        tasks = [_fetch_single(url, titles.get(url, ""), sem, client) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    pages = []
    for r in results:
        if isinstance(r, FetchedPage):
            pages.append(r)
    return pages


async def _fetch_single(
    url: str,
    title: str,
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
) -> FetchedPage | None:
    async with sem:
        try:
            resp = await client.get(url)
        except Exception as exc:
            logger.debug("fetch failed %s: %s", url, exc)
            return None

    robots_tag = resp.headers.get("x-robots-tag", "")
    if "noindex" in robots_tag or robots_tag.strip() == "none":
        return None

    try:
        html = resp.text
    except Exception:
        return None

    text = _extract_text(html)
    return FetchedPage(
        url=url,
        title=title,
        html=html,
        text=text,
        status_code=resp.status_code,
    )


def build_page(url: str, title: str, html: str) -> FetchedPage:
    """Wrap raw HTML (e.g. from the browser fetcher) into a FetchedPage."""
    return FetchedPage(
        url=url,
        title=title,
        html=html,
        text=_extract_text(html),
        status_code=200,
    )


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_REMOVE_TAGS):
        tag.decompose()
    text = soup.get_text(separator=" ")
    text = _WS_RE.sub(" ", text).strip()
    return text[:8000]
