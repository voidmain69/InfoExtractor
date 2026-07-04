from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.config import settings
from app.domain.page import FetchedPage, SearxNGResponse
from app.domain.product import ProductQuery
from app.domain.responses import SpecsResponse
from app.domain.brand_domains import official_domains
from app.domain.specs import SpecGroup
from app.extraction.all_specs import extract_all_specs, merge_spec_groups
from app.infrastructure.query.query_builder import QueryBuilder
from app.infrastructure.search.searxng import SearxNGClient
from app.services.official_site import OfficialSiteResolver
from app.services.product_match import keep_relevant as _keep_relevant_scored
from app.services.source_ranking import rank_sources
from app.services.url_filter import url_matches_domain

logger = logging.getLogger(__name__)

FetchPages = Callable[[list[str], dict[str, str]], Awaitable[list[FetchedPage]]]
FetchWithJS = Callable[[str], Awaitable[str | None]]

_MERGE_TOP_N = 3  # merge specs from up to this many best pages
_MAX_SPEC_LINKS = 2  # follow at most this many discovered spec sub-pages

# Anchor-text markers (multi-language, vendor-agnostic) of a link that leads to a
# dedicated specification page. Matched against the LINK TEXT — not the href — so
# we jump to the real spec table rather than a marketing landing, without
# hardcoding any site. `_SPEC_LINK_NEG` text rules out look-alikes.
_SPEC_LINK_KEYWORDS = (
    "technical specifications", "full specifications", "specifications", "specification",
    "tech specs", "tech spec", "techspec", "specs", "spec sheet", "datasheet", "data sheet",
    "технічні характеристики", "технические характеристики", "характеристики", "характеристика",
    "специфікації", "спецификации", "параметри", "параметры",
)
_SPEC_LINK_NEG = (
    "support", "download", "driver", "manual", "warranty", "review", "faq",
    "where to buy", "buy", "community", "news", "blog", "compare",
)


def _find_spec_links(html: str, base_url: str, limit: int = _MAX_SPEC_LINKS) -> list[str]:
    """Same-site links whose anchor text names a specification page. Returns
    absolute URLs (best match first), excluding the page itself. Vendor-agnostic:
    keyed on link wording, so it works for any site that splits specs onto a
    dedicated tab/sub-page."""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    base = urlparse(base_url)
    base_norm = base_url.split("#")[0].rstrip("/")
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        text = " ".join((a.get_text(" ", strip=True) or "").split()).lower()
        if not text or any(n in text for n in _SPEC_LINK_NEG):
            continue
        hits = [k for k in _SPEC_LINK_KEYWORDS if k in text]
        if not hits:
            continue
        href = a["href"].strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absu = urljoin(base_url, href)
        pu = urlparse(absu)
        if pu.scheme not in ("http", "https") or (pu.hostname or "") != (base.hostname or ""):
            continue
        norm = absu.split("#")[0]
        if norm.rstrip("/") == base_norm:
            continue
        scored[norm] = max(scored.get(norm, 0), max(len(k) for k in hits))  # prefer most specific wording
    return [u for u, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:limit]]


class SpecsNotFound(Exception):
    """Raised when no search results are available to extract specs from."""


def _specs_score(groups: list[SpecGroup]) -> int:
    return sum(len(g.specs) for g in groups) * 2 + len(groups) * 3


def _keep_relevant(product: ProductQuery, pages: list[FetchedPage]) -> list[FetchedPage]:
    """Drop pages about a different product (shared floor logic — a page for a
    sibling model must not become the merged 'best' source)."""
    return _keep_relevant_scored(product, pages)


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
            *[self._searxng.search(q, num_results=10) for q in queries]
        )
        ranked = rank_sources(
            responses,
            official_domain if official_only else None,
            brand_domains=official_domains(product.brand),
            model_hints=[h for h in (product.name, product.article, product.mpn) if h],
        )

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

    async def _extract_best(self, target: str) -> tuple[int, list[SpecGroup], str | None]:
        """Best static-or-Playwright spec extraction for one URL. Returns
        (score, groups, html_used) — html is the markup the winning result came
        from (rendered when JS was needed), used for spec-link discovery."""
        pages = await self._fetch_pages([target], {target: ""})
        html = pages[0].html if pages else None
        groups = extract_all_specs(html) if html else []
        score = _specs_score(groups)

        if score < settings.playwright_score_threshold:
            js_html = await self._fetch_with_js(target)
            if js_html:
                js_groups = extract_all_specs(js_html)
                if _specs_score(js_groups) > score:
                    groups, score, html = js_groups, _specs_score(js_groups), js_html
        return score, groups, html

    async def get_specs_from_url(self, url: str) -> SpecsResponse:
        """Extract specs from a single operator-supplied product-page URL.

        Bypasses search: fetch the page (validated by the same SSRF guard),
        extract statically with a Playwright fallback, and — universally, for any
        vendor — if the page links to a dedicated *specification* sub-page (matched
        by anchor wording), follow it. A real spec page beats a marketing landing,
        so a credible spec sub-page wins over the originally-supplied page."""
        primary_score, primary_groups, primary_html = await self._extract_best(url)

        # Universal "landing → spec page": prefer a dedicated specification
        # sub-page when one is linked and yields a credible result. Comparing raw
        # scores alone is unsafe (a content-heavy marketing page can out-count a
        # spec table), so an explicitly spec-labelled page is trusted on its own.
        spec_score, spec_groups, spec_url = 0, [], None
        for link in _find_spec_links(primary_html or "", url):
            s, g, _ = await self._extract_best(link)
            if g and s >= settings.playwright_score_threshold and s > spec_score:
                spec_score, spec_groups, spec_url = s, g, link

        if spec_groups:
            logger.info(
                "/specs/from-url: using spec page %s (score=%d) over %s (score=%d)",
                spec_url, spec_score, url, primary_score,
            )
            best_groups, best_url = spec_groups, spec_url
        elif primary_groups:
            best_groups, best_url = primary_groups, url
        else:
            raise SpecsNotFound(f"No specs extracted from {url}")

        return SpecsResponse(
            product=ProductQuery(name=(best_url or url)[:300]),
            groups=best_groups,
            source_url=best_url,
            total_specs=sum(len(g.specs) for g in best_groups),
        )
