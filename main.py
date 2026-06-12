import asyncio
import logging
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query

import cache as cache_store
import searxng_client
from extractors import run_pipeline
from models import AttributeResponse, SearxNGResponse
from official_site_resolver import resolve_official_domain
from page_fetcher import fetch_pages
from query_builder import build_queries
from reconciler import reconcile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="getAttrService", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/search")
async def search_proxy(q: str = Query(..., description="Search query")):
    result = await searxng_client.search(q)
    return result


@app.get("/attribute", response_model=AttributeResponse)
async def get_attribute(
    product: str = Query(..., description="Product name, e.g. 'ASUS H610M-K'"),
    attribute: str = Query(..., description="Attribute to find, e.g. 'rear USB ports'"),
    max_sources: int = Query(default=5, ge=1, le=10),
    official_only: bool = Query(default=False, description="Restrict results to manufacturer's official site"),
):
    cache_key = cache_store.make_key(product, attribute, max_sources, official_only)
    cached = cache_store.get(cache_key)
    if cached is not None:
        cached.cached = True
        return cached

    official_domain: str | None = None
    used_fallback = False

    if official_only:
        official_domain = await resolve_official_domain(product)
        if official_domain:
            logger.info("official_only mode: domain=%s", official_domain)
        else:
            logger.warning("official_only: could not resolve domain, using fallback")

    queries = await build_queries(product, attribute, official_domain=official_domain)
    logger.info("queries: %s", queries)

    search_tasks = [searxng_client.search(q, num_results=10) for q in queries]
    search_results: list[SearxNGResponse] = await asyncio.gather(*search_tasks)

    merged_infoboxes = []
    merged_answers = []
    seen_urls: dict[str, str] = {}

    for resp in search_results:
        if not merged_infoboxes:
            merged_infoboxes = resp.infoboxes
        if not merged_answers:
            merged_answers = resp.answers
        for r in resp.results:
            if r.url not in seen_urls:
                seen_urls[r.url] = r.title

    # Filter to official domain if requested
    if official_only and official_domain:
        official_urls = {
            url: title
            for url, title in seen_urls.items()
            if _url_matches_domain(url, official_domain)
        }
        if official_urls:
            seen_urls = official_urls
            logger.info("official_only: %d URLs from %s", len(official_urls), official_domain)
        else:
            # Fallback: no official results → run normal search
            logger.warning("official_only: no results from %s, falling back", official_domain)
            used_fallback = True
            fallback_queries = await build_queries(product, attribute, official_domain=None)
            fallback_tasks = [searxng_client.search(q, num_results=10) for q in fallback_queries]
            fallback_results: list[SearxNGResponse] = await asyncio.gather(*fallback_tasks)
            seen_urls = {}
            for resp in fallback_results:
                if not merged_infoboxes:
                    merged_infoboxes = resp.infoboxes
                if not merged_answers:
                    merged_answers = resp.answers
                for r in resp.results:
                    if r.url not in seen_urls:
                        seen_urls[r.url] = r.title
            queries = fallback_queries

    merged_response = SearxNGResponse(
        results=list(search_results[0].results) if search_results else [],
        infoboxes=merged_infoboxes,
        answers=merged_answers,
    )

    top_urls = list(seen_urls.keys())[:max_sources]
    if not top_urls:
        raise HTTPException(status_code=404, detail="No search results found")

    pages = await fetch_pages(top_urls, seen_urls)
    candidates = await run_pipeline(product, attribute, merged_response, pages)
    value, unit, confidence, sources = reconcile(candidates)

    response = AttributeResponse(
        product=product,
        attribute=attribute,
        value=value,
        unit=unit,
        confidence=confidence,
        sources=sources,
        search_queries_used=queries,
        official_domain=official_domain if official_only else None,
        official_only_fallback=used_fallback,
        cached=False,
    )

    if value is not None:
        cache_store.set(cache_key, response)

    return response


def _url_matches_domain(url: str, domain: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        host = host.lower().lstrip("www.")
        return host == domain or host.endswith("." + domain)
    except Exception:
        return False
