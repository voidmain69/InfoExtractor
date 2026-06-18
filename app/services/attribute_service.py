from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.domain.extraction import ExtractionCandidate
from app.domain.page import FetchedPage, SearxNGResponse
from app.domain.product import ProductQuery
from app.domain.responses import AttributeResponse
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.reconciler import reconcile
from app.extraction.value_cleaner import clean_value
from app.infrastructure.cache.ttl_cache import TTLCacheStore, make_key
from app.infrastructure.fetch.http_fetcher import build_page
from app.infrastructure.query.query_builder import QueryBuilder
from app.infrastructure.search.searxng import SearxNGClient
from app.services.official_site import OfficialSiteResolver
from app.services.product_match import MATCH_FLOOR, match_score
from app.services.source_ranking import rank_sources
from app.services.url_filter import url_matches_domain

logger = logging.getLogger(__name__)

FetchPages = Callable[[list[str], dict[str, str]], Awaitable[list[FetchedPage]]]
FetchWithJS = Callable[[str], Awaitable[str | None]]

# Below this final confidence (or when nothing was found) we try a JS render.
_JS_FALLBACK_CONF = 0.4


class AttributeNotFound(Exception):
    """Raised when no search results are available to extract from."""


class AttributeService:
    def __init__(
        self,
        searxng: SearxNGClient,
        query_builder: QueryBuilder,
        official_site: OfficialSiteResolver,
        pipeline: ExtractionPipeline,
        cache: TTLCacheStore,
        fetch_pages: FetchPages,
        fetch_with_js: FetchWithJS,
    ):
        self._searxng = searxng
        self._query_builder = query_builder
        self._official_site = official_site
        self._pipeline = pipeline
        self._cache = cache
        self._fetch_pages = fetch_pages
        self._fetch_with_js = fetch_with_js

    async def get_attribute(
        self,
        product: ProductQuery,
        attribute: str,
        max_sources: int = 5,
        official_only: bool = False,
    ) -> AttributeResponse:
        cache_key = make_key(product, attribute, max_sources, official_only)
        cached = self._cache.get(cache_key)
        if cached is not None:
            cached.cached = True
            return cached

        official_domain: str | None = None
        used_fallback = False

        if official_only:
            official_domain = await self._official_site.resolve(product)
            if official_domain:
                logger.info("official_only mode: domain=%s", official_domain)
            else:
                logger.warning("official_only: could not resolve domain, using fallback")

        queries = await self._query_builder.build_queries(
            product, attribute, official_domain=official_domain
        )
        logger.info("queries: %s", queries)

        responses = await self._search_all(queries)
        merged_infoboxes, merged_answers = _merge_boxes(responses)
        ranked = rank_sources(responses, official_domain if official_only else None)

        # Restrict to official domain (with graceful fallback to a normal search).
        if official_only and official_domain:
            official_ranked = [(u, t) for u, t in ranked if url_matches_domain(u, official_domain)]
            if official_ranked:
                ranked = official_ranked
                logger.info("official_only: %d URLs from %s", len(official_ranked), official_domain)
            else:
                logger.warning("official_only: no results from %s, falling back", official_domain)
                used_fallback = True
                queries = await self._query_builder.build_queries(product, attribute, official_domain=None)
                responses = await self._search_all(queries)
                merged_infoboxes, merged_answers = _merge_boxes(responses)
                ranked = rank_sources(responses, None)

        if not ranked:
            raise AttributeNotFound("No search results found")

        titles = dict(ranked)
        top_urls = [u for u, _ in ranked][:max_sources]

        merged_response = SearxNGResponse(
            results=list(responses[0].results) if responses else [],
            infoboxes=merged_infoboxes,
            answers=merged_answers,
        )

        pages = await self._fetch_pages(top_urls, titles)
        weights = {p.url: match_score(product, p) for p in pages}
        kept = _keep_relevant(pages, weights)

        candidates = await self._pipeline.run(product, attribute, merged_response, kept)
        candidates = _apply_weights(candidates, weights)
        value, unit, confidence, sources = reconcile(candidates)

        # JS-render fallback: only when the static-HTML pass came up weak.
        if (value is None or confidence < _JS_FALLBACK_CONF) and kept:
            value, unit, confidence, sources = await self._js_retry(
                product, attribute, kept[0], merged_response, candidates,
                value, unit, confidence, sources,
            )

        if value is not None:
            value, derived_unit = clean_value(value, attribute)
            if unit is None:
                unit = derived_unit

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
            self._cache.set(cache_key, response)

        return response

    async def _search_all(self, queries: list[str]) -> list[SearxNGResponse]:
        return await asyncio.gather(*[self._searxng.search(q, num_results=10) for q in queries])

    async def _js_retry(
        self,
        product: ProductQuery,
        attribute: str,
        page: FetchedPage,
        merged_response: SearxNGResponse,
        prior_candidates: list[ExtractionCandidate],
        value, unit, confidence, sources,
    ):
        logger.info("attribute: weak result (conf=%.2f), trying Playwright on %s", confidence, page.url)
        js_html = await self._fetch_with_js(page.url)
        if not js_html:
            return value, unit, confidence, sources

        js_page = build_page(page.url, page.title, js_html)
        js_candidates = await self._pipeline.run(product, attribute, merged_response, [js_page])
        js_candidates = _apply_weights(js_candidates, {page.url: match_score(product, js_page)})

        combined = prior_candidates + js_candidates
        v2, u2, c2, s2 = reconcile(combined)
        logger.info("attribute: playwright conf=%.2f vs static conf=%.2f", c2, confidence)
        if (value is None and v2 is not None) or c2 > confidence:
            return v2, u2, c2, s2
        return value, unit, confidence, sources


def _merge_boxes(responses: list[SearxNGResponse]) -> tuple[list, list]:
    infoboxes: list = []
    answers: list = []
    for resp in responses:
        if not infoboxes:
            infoboxes = resp.infoboxes
        if not answers:
            answers = resp.answers
    return infoboxes, answers


def _keep_relevant(pages: list[FetchedPage], weights: dict[str, float]) -> list[FetchedPage]:
    """Drop pages that are clearly about a different product; never return empty."""
    relevant = [p for p in pages if weights.get(p.url, 0.0) >= MATCH_FLOOR]
    if relevant:
        return relevant
    if pages:
        return [max(pages, key=lambda p: weights.get(p.url, 0.0))]
    return []


def _apply_weights(
    candidates: list[ExtractionCandidate],
    weights: dict[str, float],
) -> list[ExtractionCandidate]:
    """Scale each candidate's confidence by how well its source page matched."""
    for c in candidates:
        w = weights.get(c.source.url, 1.0)
        if w < 0.999:
            scaled = round(c.confidence * w, 4)
            c.confidence = scaled
            c.source.confidence = scaled
    return candidates
