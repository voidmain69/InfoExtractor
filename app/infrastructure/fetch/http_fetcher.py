import asyncio
import logging
import random
import re

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.domain.page import FetchedPage
from app.extraction.text_repair import fix_text
from app.infrastructure.fetch.url_guard import is_safe_url

logger = logging.getLogger(__name__)

_REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
_WS_RE = re.compile(r"\s{2,}")
_MAX_REDIRECTS = 5


class _RawResponse:
    """Minimal response holder: redirects are followed manually so the raw
    httpx.Response is consumed inside its streaming context."""

    __slots__ = ("status_code", "headers", "text")

    def __init__(self, status_code: int, headers: dict, text: str):
        self.status_code = status_code
        self.headers = headers
        self.text = text

# Realistic modern browser profiles. Each pairs a User-Agent with the matching
# Client Hints (Sec-CH-UA*) so anti-bot systems that cross-check the two see a
# consistent fingerprint — a Chrome UA sending no/Firefox client hints is an
# obvious tell. Firefox and Safari intentionally carry no Client Hints because
# they don't send them in real traffic.
_UA_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "ch_ua": '"Microsoft Edge";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"macOS"',
    },
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"Linux"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "ch_ua": None,
        "platform": None,
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "ch_ua": None,
        "platform": None,
    },
]

# Flat list kept for the browser fetcher and the anti-block smoke tests.
_USER_AGENTS = [p["ua"] for p in _UA_PROFILES]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.9,uk;q=0.8",
    "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.8",
]

# Arriving "from a search" looks more organic than a cold direct hit and matches
# how product/spec pages are normally reached.
_REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
]

# HTTP status codes that are worth retrying.
_RETRY_STATUSES = {429, 503, 502, 403}


def _random_headers() -> dict[str, str]:
    profile = random.choice(_UA_PROFILES)
    headers = {
        "User-Agent": profile["ua"],
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        # Omit "br" — brotli decompression needs the optional brotlicffi package.
        # httpx sets Accept-Encoding automatically when decompression is available.
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Referer": random.choice(_REFERERS),
    }
    # Client Hints only for Chromium-family UAs, kept consistent with the UA.
    if profile["ch_ua"]:
        headers["Sec-CH-UA"] = profile["ch_ua"]
        headers["Sec-CH-UA-Mobile"] = "?0"
        headers["Sec-CH-UA-Platform"] = profile["platform"]
    return headers


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
    # follow_redirects=False on purpose: we follow manually so every hop's host
    # is re-validated against the SSRF guard (a 3xx is the classic bypass).
    async with httpx.AsyncClient(
        timeout=settings.page_fetch_timeout_seconds,
        follow_redirects=False,
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

    html = resp.text
    text = _extract_text(html)
    return FetchedPage(
        url=url,
        title=title,
        html=html,
        text=text,
        status_code=resp.status_code,
    )


async def _read_capped(resp: httpx.Response) -> str | None:
    """Read a streamed response body, bailing out past max_page_bytes.

    Prevents a malicious/huge URL from exhausting memory: only the cap is ever
    buffered, and Content-Length is short-circuited before any read.
    """
    cap = settings.max_page_bytes
    cl = resp.headers.get("content-length")
    if cl:
        try:
            if int(cl) > cap:
                logger.debug("skip oversized response (%s bytes): %s", cl, resp.url)
                return None
        except ValueError:
            pass
    total = 0
    chunks: list[bytes] = []
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > cap:
            logger.debug("response exceeded %d bytes, dropping: %s", cap, resp.url)
            return None
        chunks.append(chunk)
    raw = b"".join(chunks)
    encoding = resp.encoding or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        return raw.decode("utf-8", errors="replace")


async def _request_once(
    client: httpx.AsyncClient, url: str
) -> _RawResponse | None:
    """Issue a GET, following redirects manually and validating every hop.

    Returns None when a hop is blocked by the SSRF guard, the body is too
    large, or the redirect chain is too long.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not await is_safe_url(current):
            logger.warning("blocked unsafe fetch URL: %s", current)
            return None
        async with client.stream("GET", current, headers=_random_headers()) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    return _RawResponse(resp.status_code, dict(resp.headers), "")
                current = str(resp.url.join(location))
                continue
            text = await _read_capped(resp)
            if text is None:
                return None
            return _RawResponse(resp.status_code, dict(resp.headers), text)
    logger.debug("too many redirects for %s", url)
    return None


async def _get_with_retry(
    client: httpx.AsyncClient, url: str
) -> _RawResponse | None:
    attempts = max(1, settings.fetch_retry_attempts)
    backoff = settings.fetch_retry_backoff
    for attempt in range(attempts):
        try:
            resp = await _request_once(client, url)
        except Exception as exc:
            if attempt == attempts - 1:
                logger.debug("fetch failed %s: %s", url, exc)
                return None
            await asyncio.sleep(backoff * (2 ** attempt))
            continue
        # None means blocked/oversized/too-many-redirects — never retry those.
        if resp is None:
            return None
        if resp.status_code not in _RETRY_STATUSES:
            return resp
        retry_after = _parse_retry_after(resp.headers.get("retry-after"))
        wait = retry_after if retry_after else backoff * (2 ** attempt)
        logger.debug(
            "fetch %s → %d, retry %d/%d in %.1fs",
            url, resp.status_code, attempt + 1, attempts, wait,
        )
        await asyncio.sleep(wait)
    logger.debug("fetch exhausted retries for %s", url)
    return None


def _parse_retry_after(header: str | None) -> float | None:
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
    text = fix_text(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:8000]
