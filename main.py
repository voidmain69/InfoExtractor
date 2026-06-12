import asyncio
import logging
import re
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query

import cache as cache_store
import searxng_client
from extractors import run_pipeline
from extractors.all_specs_extractor import extract_all_specs
from models import AttributeResponse, ProductQuery, SpecsResponse, SearxNGResponse
from official_site_resolver import resolve_official_domain
from page_fetcher import fetch_pages
from page_fetcher_js import fetch_with_js
from query_builder import build_queries, build_specs_queries
from reconciler import reconcile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="getAttrService", version="2.0.0")


def _product_query(
    name: str = Query(..., description="Product name (required)"),
    category: str | None = Query(default=None, description="Product category"),
    brand: str | None = Query(default=None, description="Brand / manufacturer"),
    article: str | None = Query(default=None, description="Article / SKU"),
    ean13: str | None = Query(default=None, description="EAN-13 barcode"),
    upc: str | None = Query(default=None, description="UPC barcode"),
) -> ProductQuery:
    return ProductQuery(
        name=name, category=category, brand=brand,
        article=article, ean13=ean13, upc=upc,
    )


def _url_matches_domain(url: str, domain: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        host = re.sub(r"^www\.", "", host.lower())
        return host == domain or host.endswith("." + domain)
    except Exception:
        return False


def _specs_score(groups) -> int:
    return sum(len(g.specs) for g in groups) * 2 + len(groups) * 3


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/search")
async def search_proxy(q: str = Query(..., description="Search query")):
    return await searxng_client.search(q)


@app.get("/attribute", response_model=AttributeResponse)
async def get_attribute(
    product: ProductQuery = Depends(_product_query),
    attribute: str = Query(..., description="Attribute to find, e.g. 'rear USB ports'"),
    max_sources: int = Query(default=5, ge=1, le=10),
    official_only: bool = Query(default=False),
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

    merged_infoboxes, merged_answers = [], []
    seen_urls: dict[str, str] = {}
    for resp in search_results:
        if not merged_infoboxes:
            merged_infoboxes = resp.infoboxes
        if not merged_answers:
            merged_answers = resp.answers
        for r in resp.results:
            if r.url not in seen_urls:
                seen_urls[r.url] = r.title

    if official_only and official_domain:
        official_urls = {u: t for u, t in seen_urls.items() if _url_matches_domain(u, official_domain)}
        if official_urls:
            seen_urls = official_urls
            logger.info("official_only: %d URLs from %s", len(official_urls), official_domain)
        else:
            logger.warning("official_only: no results from %s, falling back", official_domain)
            used_fallback = True
            fallback_queries = await build_queries(product, attribute, official_domain=None)
            fallback_tasks = [searxng_client.search(q, num_results=10) for q in fallback_queries]
            fallback_results = await asyncio.gather(*fallback_tasks)
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
        product=product, attribute=attribute, value=value, unit=unit,
        confidence=confidence, sources=sources, search_queries_used=queries,
        official_domain=official_domain if official_only else None,
        official_only_fallback=used_fallback, cached=False,
    )
    if value is not None:
        cache_store.set(cache_key, response)
    return response


@app.get("/specs", response_model=SpecsResponse)
async def get_all_specs(
    product: ProductQuery = Depends(_product_query),
    official_only: bool = Query(default=False),
):
    """Extract all product specifications as structured groups."""
    official_domain: str | None = None
    if official_only:
        official_domain = await resolve_official_domain(product)
        if official_domain:
            logger.info("/specs official_only: domain=%s", official_domain)

    queries = await build_specs_queries(product, official_domain=official_domain)
    logger.info("/specs queries: %s", queries)

    search_tasks = [searxng_client.search(q, num_results=8) for q in queries]
    search_results = await asyncio.gather(*search_tasks)

    seen_urls: dict[str, str] = {}
    for resp in search_results:
        for r in resp.results:
            if r.url not in seen_urls:
                seen_urls[r.url] = r.title

    if official_only and official_domain:
        filtered = {u: t for u, t in seen_urls.items() if _url_matches_domain(u, official_domain)}
        if filtered:
            seen_urls = filtered

    top_urls = list(seen_urls.keys())[:4]
    if not top_urls:
        raise HTTPException(status_code=404, detail="No search results found")

    pages = await fetch_pages(top_urls, seen_urls)

    best_groups = []
    best_url: str | None = None
    best_score = 0

    for page in pages:
        groups = extract_all_specs(page.html)
        score = _specs_score(groups)
        if score > best_score:
            best_score = score
            best_groups = groups
            best_url = page.url

    # Playwright fallback when httpx extraction is sparse
    if best_score < 20 and top_urls:
        logger.info("/specs: sparse result (score=%d), trying Playwright on %s", best_score, top_urls[0])
        js_html = await fetch_with_js(top_urls[0])
        if js_html:
            js_groups = extract_all_specs(js_html)
            js_score = _specs_score(js_groups)
            logger.info("/specs: playwright score=%d vs httpx score=%d", js_score, best_score)
            if js_score > best_score:
                best_groups = js_groups
                best_url = top_urls[0]

    return SpecsResponse(
        product=product,
        groups=best_groups,
        source_url=best_url,
        total_specs=sum(len(g.specs) for g in best_groups),
    )
