from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.core.config import settings
from app.domain.page import FetchedPage, SearxNGResponse
from app.domain.product import ProductQuery
from app.domain.responses import SpecsResponse
from app.domain.specs import SpecGroup
from app.extraction.all_specs import extract_all_specs, merge_spec_groups
from app.infrastructure.query.query_builder import QueryBuilder
from app.infrastructure.search.searxng import SearxNGClient
from app.services.official_site import OfficialSiteResolver
from app.services.product_match import MATCH_FLOOR, match_score
from app.services.source_ranking import rank_sources
from app.services.url_filter import url_matches_domain

logger = logging.getLogger(__name__)

FetchPages = Callable[[list[str], dict[str, str]], Awaitable[list[FetchedPage]]]
FetchWithJS = Callable[[str], Awaitable[str | None]]

_MERGE_TOP_N = 3  # merge specs from up to this many best pages


class SpecsNotFound(Exception):
    """Raised when no search results are available to extract specs from."""


def _specs_score(groups: list[SpecGroup]) -> int:
    return sum(len(g.specs) for g in groups) * 2 + len(groups) * 3


def _keep_relevant(product: ProductQuery, pages: list[FetchedPage]) -> list[FetchedPage]:
    """Drop pages about a different product; never return empty."""
    relevant = [p for p in pages if match_score(product, p) >= MATCH_FLOOR]
    if relevant:
        return relevant
    if pages:
        return [max(pages, key=lambda p: match_score(product, p))]
    return []


class SpecsService:
    def __init__(
        self,
        searxng: SearxNGClient,
        query_builder: QueryBuilder,
        official_site: OfficialSiteResolver,
        fetch_pages: FetchPages,
        fetch_with_js: FetchWithJS,
    ):
        self._searxng = searxng
        self._query_builder = query_builder
        self._official_site = official_site
        self._fetch_pages = fetch_pages
        self._fetch_with_js = fetch_with_js

    async def get_specs(
        self,
        product: ProductQuery,
        official_only: bool = False,
    ) -> SpecsResponse:
        official_domain: str | None = None
        if official_only:
            official_domain = await self._official_site.resolve(product)
            if official_domain:
                logger.info("/specs official_only: domain=%s", official_domain)

        queries = await self._query_builder.build_specs_queries(
            product, official_domain=official_domain
        )
        logger.info("/specs queries: %s", queries)

        responses: list[SearxNGResponse] = await asyncio.gather(
            *[self._searxng.search(q, num_results=8) for q in queries]
        )
        ranked = rank_sources(responses, official_domain if official_only else None)

        if official_only and official_domain:
            filtered = [(u, t) for u, t in ranked if url_matches_domain(u, official_domain)]
            if filtered:
                ranked = filtered

        if not ranked:
            raise SpecsNotFound("No search results found")

        titles = dict(ranked)
        top_urls = [u for u, _ in ranked][:4]
        pages = await self._fetch_pages(top_urls, titles)

        # Keep only pages that are actually about this product — otherwise the
        # cross-page merge below would pull specs from unrelated items (a case,
        # a different model variant, an accessory).
        pages = _keep_relevant(product, pages)

        # Score every page; merge specs from the best few for fuller coverage.
        scored: list[tuple[int, str, list[SpecGroup]]] = []
        for page in pages:
            groups = extract_all_specs(page.html)
            score = _specs_score(groups)
            if score > 0:
                scored.append((score, page.url, groups))
        scored.sort(key=lambda x: x[0], reverse=True)

        if scored:
            best_score, best_url = scored[0][0], scored[0][1]
            best_groups = merge_spec_groups([g for _, _, g in scored[:_MERGE_TOP_N]])
        else:
            best_score, best_url, best_groups = 0, None, []

        # Playwright fallback when static extraction is sparse. The headless
        # browser reveals specs hidden behind tabs/accordions/"show more", so
        # render the top few URLs (not just one) and merge whatever each yields.
        if best_score < settings.playwright_score_threshold and top_urls:
            js_urls = top_urls[: max(1, settings.playwright_max_urls)]
            logger.info(
                "/specs: sparse result (score=%d), trying Playwright on %d url(s): %s",
                best_score, len(js_urls), js_urls,
            )
            js_htmls = await asyncio.gather(*[self._fetch_with_js(u) for u in js_urls])

            js_scored: list[tuple[int, str, list[SpecGroup]]] = []
            for url, html in zip(js_urls, js_htmls):
                if not html:
                    continue
                groups = extract_all_specs(html)
                score = _specs_score(groups)
                if score > 0:
                    js_scored.append((score, url, groups))
            js_scored.sort(key=lambda x: x[0], reverse=True)

            if js_scored and js_scored[0][0] > best_score:
                logger.info(
                    "/specs: best playwright score=%d vs static score=%d",
                    js_scored[0][0], best_score,
                )
                # JS pages first (best wins on conflicts), then the static top.
                best_groups = merge_spec_groups(
                    [g for _, _, g in js_scored] + [g for _, _, g in scored[:_MERGE_TOP_N]]
                )
                best_url = js_scored[0][1]

        return SpecsResponse(
            product=product,
            groups=best_groups,
            source_url=best_url,
            total_specs=sum(len(g.specs) for g in best_groups),
        )
