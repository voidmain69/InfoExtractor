import asyncio
import logging
import random
import re

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.domain.page import FetchedPage

logger = logging.getLogger(__name__)

_REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
_WS_RE = re.compile(r"\s{2,}")

# Realistic modern browser User-Agents (rotated per request).
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.9,uk;q=0.8",
    "en-US,en;q=0.8",
]

# HTTP status codes that are worth retrying.
_RETRY_STATUSES = {429, 503, 502, 403}


def _random_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }


def _pick_proxy() -> str | None:
    raw = settings.proxy_list.strip()
    if not raw:
        return None
    proxies = [p.strip() for p in raw.split(",") if p.strip()]
    return random.choice(proxies) if proxies else None


async def fetch_pages(
    urls: list[str],
    titles: dict[str, str],
) -> list[FetchedPage]:
    sem = asyncio.Semaphore(settings.max_concurrent_fetches)
    proxy = _pick_proxy()
    async with httpx.AsyncClient(
        timeout=settings.page_fetch_timeout_seconds,
        follow_redirects=True,
        proxy=proxy,
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
        if settings.fetch_jitter_max > 0:
            await asyncio.sleep(random.uniform(0, settings.fetch_jitter_max))

        resp = await _get_with_retry(client, url)
        if resp is None:
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


async def _get_with_retry(
    client: httpx.AsyncClient, url: str
) -> httpx.Response | None:
    attempts = max(1, settings.fetch_retry_attempts)
    backoff = settings.fetch_retry_backoff
    for attempt in range(attempts):
        try:
            resp = await client.get(url, headers=_random_headers())
            if resp.status_code not in _RETRY_STATUSES:
                return resp
            retry_after = _parse_retry_after(resp)
            wait = retry_after if retry_after else backoff * (2 ** attempt)
            logger.debug(
                "fetch %s → %d, retry %d/%d in %.1fs",
                url, resp.status_code, attempt + 1, attempts, wait,
            )
            await asyncio.sleep(wait)
        except Exception as exc:
            if attempt == attempts - 1:
                logger.debug("fetch failed %s: %s", url, exc)
                return None
            await asyncio.sleep(backoff * (2 ** attempt))
    logger.debug("fetch exhausted retries for %s", url)
    return None


def _parse_retry_after(resp: httpx.Response) -> float | None:
    header = resp.headers.get("retry-after")
    if not header:
        return None
    try:
        return min(float(header), 30.0)
    except ValueError:
        return None


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
