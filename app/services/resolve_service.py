"""Batch attribute resolution: fetch a product's pages once, resolve many typed
attributes against that shared content, then normalize values with the AI layer."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.core.config import settings
from app.domain.attributes import AttributeSpec, AttrType, ResolveStatus, ResolvedAttribute
from app.domain.extraction import ExtractionCandidate, SourceResult
from app.domain.page import FetchedPage, SearxNGResponse
from app.domain.product import ProductQuery
from app.domain.responses import ResolveResponse
from app.extraction.coerce import (
    coerce_boolean,
    coerce_integer,
    coerce_number,
    snap_enum,
)
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.reconciler import reconcile
from app.extraction.value_cleaner import clean_value, select_segment
from app.infrastructure.cache.ttl_cache import TTLCacheStore, make_key
from app.infrastructure.fetch.http_fetcher import build_page
from app.infrastructure.query.query_builder import QueryBuilder
from app.infrastructure.search.searxng import SearxNGClient
from app.services.attribute_matcher import (
    PooledSpec,
    candidates_for_label,
    dimension_candidates,
    match_in_pool,
    page_pool,
    pool_labels,
    text_pool,
)
from app.domain.brand_domains import official_domains
from app.services.official_site import OfficialSiteResolver
from app.services.product_match import keep_relevant
from app.services.semantic_matcher import SemanticMatcher
from app.services.synonyms import find_synonym_label
from app.services.source_ranking import rank_sources
from app.services.url_filter import url_matches_domain
from app.services.value_normalizer import NormItem, ValueNormalizer

logger = logging.getLogger(__name__)

FetchPages = Callable[[list[str], dict[str, str]], Awaitable[list[FetchedPage]]]
FetchWithJS = Callable[[str], Awaitable[str | None]]


@dataclass
class _Pool:
    pages: list[FetchedPage]
    merged: SearxNGResponse
    specs: list[PooledSpec]
    productive_pages: int = 0


@dataclass
class _Raw:
    raw_value: str | None
    confidence: float
    source_url: str | None
    sources: list[SourceResult]


class ResolveService:
    def __init__(
        self,
        searxng: SearxNGClient,
        query_builder: QueryBuilder,
        official_site: OfficialSiteResolver,
        pipeline: ExtractionPipeline,
        normalizer: ValueNormalizer,
        semantic_matcher: SemanticMatcher,
        cache: TTLCacheStore,
        fetch_pages: FetchPages,
        fetch_with_js: FetchWithJS | None = None,
    ):
        self._searxng = searxng
        self._query_builder = query_builder
        self._official_site = official_site
        self._pipeline = pipeline
        self._normalizer = normalizer
        self._semantic_matcher = semantic_matcher
        self._cache = cache
        self._fetch_pages = fetch_pages
        self._fetch_with_js = fetch_with_js
        self._sem = asyncio.Semaphore(max(1, settings.resolve_max_concurrency))

    async def resolve(
        self,
        product: ProductQuery,
        attributes: list[AttributeSpec],
        official_only: bool = False,
        max_sources: int = 5,
    ) -> ResolveResponse:
        # 1. Per-attribute cache check.
        results: dict[int, ResolvedAttribute] = {}
        misses: list[tuple[int, AttributeSpec]] = []
        for i, spec in enumerate(attributes):
            cached = self._cache.get(_cache_key(product, spec, max_sources, official_only))
            if cached is not None:
                results[i] = cached
            else:
                misses.append((i, spec))

        if misses:
            pool = await self._build_pool(product, official_only, max_sources)
            resolved = await self._resolve_against_pool(
                product, [s for _, s in misses], pool,
                official_only=official_only, max_sources=max_sources, allow_targeted=True,
            )
            for (i, spec), res in zip(misses, resolved):
                results[i] = res
                if res.status == ResolveStatus.FOUND:
                    self._cache.set(_cache_key(product, spec, max_sources, official_only), res)

        ordered = [results[i] for i in range(len(attributes))]
        return ResolveResponse(product=product, results=ordered, cached=not misses)

    async def resolve_from_url(
        self, url: str, attributes: list[AttributeSpec]
    ) -> ResolveResponse:
        """Resolve typed attributes from a single operator-supplied product URL.

        Bypasses search entirely: the page (+ Playwright reveal when sparse) is the
        only source. The full downstream pipeline — synonym/fuzzy/semantic match,
        deterministic coercion, unit conversion, enum snap, reconcile, provenance —
        runs unchanged. `allow_targeted=False`: no per-attribute web fallback, since
        the operator explicitly pinned the source."""
        product = ProductQuery(name=url[:300])
        pool = await self._build_pool_from_url(url)
        resolved = await self._resolve_against_pool(
            product, attributes, pool,
            official_only=False, max_sources=0, allow_targeted=False,
        )
        return ResolveResponse(product=product, results=resolved, cached=False)

    async def resolve_from_text(
        self, text: str, attributes: list[AttributeSpec]
    ) -> ResolveResponse:
        """Resolve typed attributes from operator-supplied text (parsed file
        content). Deterministic-first: a spec pool is built from "Label: value"
        lines, then coerced/unit-converted/enum-snapped. No search and no page
        pipeline — a bare text blob has no DOM to LLM-extract, so unmatched
        attributes come back not_found for the caller to handle."""
        product = ProductQuery(name="imported-text")
        pool = self._build_pool_from_text(text)
        resolved = await self._resolve_against_pool(
            product, attributes, pool,
            official_only=False, max_sources=0, allow_targeted=False,
        )
        return ResolveResponse(product=product, results=resolved, cached=False)

    # ── shared resolution core (pool → typed values) ──────────────────────

    async def _resolve_against_pool(
        self,
        product: ProductQuery,
        attributes: list[AttributeSpec],
        pool: _Pool,
        official_only: bool,
        max_sources: int,
        allow_targeted: bool,
    ) -> list[ResolvedAttribute]:
        """Resolve a list of typed attributes against an already-built spec pool.
        Shared by search (`resolve`), URL (`resolve_from_url`) and text
        (`resolve_from_text`) — only pool-building differs."""
        # Map every requested attribute onto the page's actual labels by meaning.
        # A curated synonym table resolves the well-known cases deterministically;
        # only the leftovers go to the LLM semantic call.
        semantic_labels: list[str | None] = [None] * len(attributes)
        labels = pool_labels(pool.specs) if pool.specs else []
        if labels:
            unresolved_idx: list[int] = []
            unresolved_names: list[str] = []
            for j, spec in enumerate(attributes):
                syn = find_synonym_label(spec.name, labels)
                if syn is not None:
                    semantic_labels[j] = syn
                else:
                    unresolved_idx.append(j)
                    unresolved_names.append(spec.name)
            if unresolved_names:
                llm_labels = await self._semantic_matcher.match(unresolved_names, labels)
                for j, lbl in zip(unresolved_idx, llm_labels):
                    semantic_labels[j] = lbl

        # Resolve raw values for all attributes (bounded concurrency).
        raw_list = await asyncio.gather(
            *[self._resolve_raw(product, spec, pool, label, official_only, max_sources, allow_targeted)
              for spec, label in zip(attributes, semantic_labels)]
        )

        # Normalize (single batched AI call) + assemble.
        return await self._normalize_all(attributes, raw_list)

    # ── shared source pool ────────────────────────────────────────────────

    async def _build_pool(
        self, product: ProductQuery, official_only: bool, max_sources: int
    ) -> _Pool:
        official_domain = None
        if official_only:
            official_domain = await self._official_site.resolve(product)

        queries = await self._query_builder.build_specs_queries(
            product, official_domain=official_domain
        )
        logger.info("/attributes queries: %s", queries)
        responses = await asyncio.gather(*[self._searxng.search(q, num_results=10) for q in queries])
        ranked = rank_sources(
            responses,
            official_domain if official_only else None,
            brand_domains=official_domains(product.brand),
            model_hints=_model_hints(product),
        )
        if official_only and official_domain:
            filtered = [(u, t) for u, t in ranked if url_matches_domain(u, official_domain)]
            if filtered:
                ranked = filtered

        titles = dict(ranked)
        top_urls = [u for u, _ in ranked][:max_sources]
        logger.info("/attributes top_urls: %s", top_urls)
        pages = await self._fetch_pages(top_urls, titles) if top_urls else []
        logger.info("/attributes fetched %d pages, statuses: %s",
                    len(pages), [(p.url.split("/")[2], p.status_code) for p in pages])
        pages = keep_relevant(product, pages)
        logger.info("/attributes keep_relevant kept %d pages", len(pages))

        # Build the pool from relevance-ordered pages, but count only pages
        # that actually yield specs toward the page cap — a support page or a
        # JS shell shouldn't crowd out the retail page with the real table.
        pool_entries: list[PooledSpec] = []
        kept_pages: list[FetchedPage] = []
        productive = 0
        for page in pages:
            entries = page_pool(page)
            if entries:
                pool_entries.extend(entries)
                kept_pages.append(page)
                productive += 1
                if productive >= settings.resolve_pool_max_pages:
                    break
        pages = kept_pages or pages[: settings.resolve_pool_max_pages]
        logger.info("/attributes spec pool: %d entries from %d productive pages",
                    len(pool_entries), productive)

        # Sparse pool → the specs are likely client-rendered or collapsed.
        # Render the top URLs with the headless browser and merge the reveal.
        if (settings.use_playwright and self._fetch_with_js
                and len(pool_entries) < settings.resolve_pool_js_threshold and top_urls):
            js_urls = top_urls[: max(1, settings.playwright_max_urls)]
            logger.info("/attributes: sparse pool (%d), JS-rendering %s",
                        len(pool_entries), js_urls)
            js_htmls = await asyncio.gather(
                *[self._fetch_with_js(u) for u in js_urls], return_exceptions=True
            )
            for url, html in zip(js_urls, js_htmls):
                if not isinstance(html, str) or not html:
                    continue
                js_page = build_page(url, titles.get(url, ""), html)
                js_entries = page_pool(js_page)
                if js_entries:
                    known = {(e.name, e.url) for e in pool_entries}
                    pool_entries.extend(
                        e for e in js_entries if (e.name, e.url) not in known
                    )
                    if all(p.url != url for p in pages):
                        pages.append(js_page)
                        productive += 1
            logger.info("/attributes: pool after JS render: %d entries", len(pool_entries))

        infoboxes, answers = _merge_boxes(responses)
        merged = SearxNGResponse(
            results=list(responses[0].results) if responses else [],
            infoboxes=infoboxes,
            answers=answers,
        )
        return _Pool(pages=pages, merged=merged, specs=pool_entries,
                     productive_pages=productive)

    async def _build_pool_from_url(self, url: str) -> _Pool:
        """Build a spec pool from a single operator-supplied URL (no search).
        Fetch the page, extract specs, and — when the static pool is sparse —
        JS-render with Playwright and merge the reveal (same sparse-pool guard as
        the search path)."""
        pages = await self._fetch_pages([url], {url: ""})
        pool_entries: list[PooledSpec] = []
        kept: list[FetchedPage] = []
        for page in pages:
            entries = page_pool(page)
            if entries:
                pool_entries.extend(entries)
                kept.append(page)

        if (settings.use_playwright and self._fetch_with_js
                and len(pool_entries) < settings.resolve_pool_js_threshold):
            logger.info("/attributes/from-url: sparse pool (%d), JS-rendering %s",
                        len(pool_entries), url)
            js_html = await self._fetch_with_js(url)
            if isinstance(js_html, str) and js_html:
                js_page = build_page(url, "", js_html)
                js_entries = page_pool(js_page)
                known = {(e.name, e.url) for e in pool_entries}
                new = [e for e in js_entries if (e.name, e.url) not in known]
                if new:
                    pool_entries.extend(new)
                    kept.append(js_page)
            logger.info("/attributes/from-url: pool after JS render: %d entries", len(pool_entries))

        return _Pool(pages=kept or pages, merged=_empty_response(),
                     specs=pool_entries, productive_pages=len(kept))

    def _build_pool_from_text(self, text: str) -> _Pool:
        """Build a spec pool from operator-supplied text (parsed file content).

        HTML content (a saved product page / pasted markup) runs through the full
        spec extractor (site-CSS, JSON-LD, embedded JSON, tables/dl/li) via
        page_pool — the same coverage as a fetched URL, minus the network. Plain
        text falls back to "Label: value" line parsing. HTML keeps a synthetic
        page so the per-attribute LLM pipeline can fill misses; plain text has no
        DOM to run it on."""
        if _looks_html(text):
            page = build_page("imported-html", "", text)
            pool_entries = page_pool(page)
            pages = [page]
            logger.info("/attributes/from-text: HTML detected -> %d specs via extract_all_specs",
                        len(pool_entries))
        else:
            pool_entries = text_pool(text, url="imported-text")
            pages = []
            logger.info("/attributes/from-text: %d spec pairs from %d chars of text",
                        len(pool_entries), len(text))
        return _Pool(pages=pages, merged=_empty_response(),
                     specs=pool_entries, productive_pages=1 if pool_entries else 0)

    # ── per-attribute raw extraction ─────────────────────────────────────

    async def _resolve_raw(
        self,
        product: ProductQuery,
        spec: AttributeSpec,
        pool: _Pool,
        semantic_label: str | None,
        official_only: bool,
        max_sources: int,
        allow_targeted: bool = True,
    ) -> _Raw:
        async with self._sem:
            # Cheap path: string-fuzzy match against the already-extracted pool …
            candidates = match_in_pool(spec.name, pool.specs, settings.resolve_match_threshold)
            # … plus the semantic match (catches synonyms / translations).
            if semantic_label:
                candidates += candidates_for_label(pool.specs, semantic_label)
            candidates = _dedup(candidates)

            # Width/height/depth asked separately but published as one
            # "Габарити (ШхВхГ)" row — parse the axis out of the blob.
            if not candidates:
                candidates = dimension_candidates(spec.name, pool.specs)

            # Pipeline path on already-fetched pages (no re-fetch).
            if not candidates and pool.pages:
                candidates = await self._pipeline.run(product, spec.name, pool.merged, pool.pages)

            # Targeted per-attribute fallback (own small search). Skip it when the
            # shared pool is already rich: the attribute is almost certainly present,
            # so a miss is a matching problem, not missing data — firing a fresh
            # search per attribute would just burst the engines back into throttling.
            # "Rich" requires corroboration from ≥2 pages: one page can bulk up the
            # count with junk (a catalog page's product cards) and would otherwise
            # silence the fallback exactly when it's needed.
            pool_is_rich = (
                len(pool.specs) >= settings.resolve_pool_rich_threshold
                and pool.productive_pages >= 2
            )
            if (not candidates and allow_targeted and settings.resolve_targeted_fallback
                    and not official_only and not pool_is_rich):
                candidates = await self._targeted(product, spec.name, max_sources)

        value, _unit, confidence, _all = reconcile(candidates)
        source_url, sources = _winning_provenance(candidates, value)
        return _Raw(raw_value=value, confidence=confidence, source_url=source_url, sources=sources)

    async def _targeted(
        self, product: ProductQuery, attribute: str, max_sources: int
    ) -> list[ExtractionCandidate]:
        try:
            # Keep the fallback cheap: fewer queries + a single best page, so it
            # can't fan out into the burst that re-triggers engine throttling.
            # Prefer open-web queries here: the shared pool already covered the
            # official site, so the fallback's value is source diversity
            # (retail/aggregator pages carrying the missing label).
            queries = await self._query_builder.build_queries(product, attribute)
            queries = ([q for q in queries if "site:" not in q] or queries)[:2]
            responses = await asyncio.gather(*[self._searxng.search(q, num_results=10) for q in queries])
            ranked = rank_sources(responses, None,
                                  brand_domains=official_domains(product.brand),
                                  model_hints=_model_hints(product))
            top_urls = [u for u, _ in ranked][:2]
            if not top_urls:
                return []
            pages = keep_relevant(product, await self._fetch_pages(top_urls, dict(ranked)))
            infoboxes, answers = _merge_boxes(responses)
            merged = SearxNGResponse(
                results=list(responses[0].results) if responses else [],
                infoboxes=infoboxes, answers=answers,
            )
            return await self._pipeline.run(product, attribute, merged, pages)
        except Exception as exc:
            logger.debug("targeted fallback failed for %s: %s", attribute, exc)
            return []

    # ── normalization ────────────────────────────────────────────────────

    async def _normalize_all(
        self, specs: list[AttributeSpec], raws: list[_Raw]
    ) -> list[ResolvedAttribute]:
        results: list[ResolvedAttribute | None] = [None] * len(specs)
        norm_items: list[NormItem] = []
        norm_index: list[int] = []  # positions that go through the LLM normalizer

        for idx, (spec, raw) in enumerate(zip(specs, raws)):
            if not raw.raw_value:
                results[idx] = _not_found(spec)
                continue
            # Deterministic coercion first — reliable and free; only defer to the
            # LLM normalizer when local parsing can't confidently resolve it.
            det = _coerce(spec, raw.raw_value)
            if det is not None:
                value, unit, matched, conf = det
                results[idx] = ResolvedAttribute(
                    name=spec.name, type=spec.type, value=value, unit=unit,
                    raw_value=raw.raw_value, matched_allowed=matched,
                    confidence=_match_boosted(round(raw.confidence * conf, 4), matched),
                    source_url=raw.source_url,
                    status=ResolveStatus.FOUND, sources=raw.sources,
                )
                continue
            norm_items.append(NormItem(
                name=spec.name, type=spec.type.value, unit=spec.unit,
                allowed_values=spec.allowed_values, raw_value=raw.raw_value,
            ))
            norm_index.append(idx)

        norm_results = await self._normalizer.normalize(norm_items)
        for pos, idx in enumerate(norm_index):
            spec, raw, norm = specs[idx], raws[idx], norm_results[pos]
            final_conf = round(raw.confidence * norm.confidence, 4)

            if spec.allowed_values and not norm.matched_allowed:
                status = ResolveStatus.AMBIGUOUS
            elif norm.value is None:
                status = ResolveStatus.NOT_FOUND
            else:
                status = ResolveStatus.FOUND
                final_conf = _match_boosted(final_conf, norm.matched_allowed)

            results[idx] = ResolvedAttribute(
                name=spec.name, type=spec.type, value=norm.value,
                unit=norm.unit or spec.unit, raw_value=raw.raw_value,
                matched_allowed=norm.matched_allowed,
                confidence=final_conf if status != ResolveStatus.NOT_FOUND else 0.0,
                source_url=raw.source_url if status != ResolveStatus.NOT_FOUND else None,
                status=status,
                sources=raw.sources if status != ResolveStatus.NOT_FOUND else [],
            )

        return [r for r in results if r is not None]


def _model_hints(product: ProductQuery) -> list[str]:
    return [h for h in (product.name, product.article, product.mpn) if h]


def _cache_key(product: ProductQuery, spec: AttributeSpec, max_sources: int, official_only: bool) -> tuple:
    return ("resolve",) + make_key(product, spec.name, max_sources, official_only) + (
        spec.type.value,
        (spec.unit or "").lower(),
        tuple(v.lower() for v in (spec.allowed_values or [])),
    )


def _dedup(candidates: list[ExtractionCandidate]) -> list[ExtractionCandidate]:
    """Drop duplicate (url, value) candidates so a spec matched by both the fuzzy
    and semantic path isn't counted twice."""
    seen: set[tuple[str, str]] = set()
    out: list[ExtractionCandidate] = []
    for c in candidates:
        key = (c.source.url, c.value)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


_HTML_HINT_RE = re.compile(r"<(?:table|html|body|div|ul|dl|section|script|article)\b", re.I)


def _looks_html(text: str) -> bool:
    """Does this text carry HTML markup worth extracting structurally (tables /
    JSON-LD / embedded JSON) rather than parsing as 'Label: value' lines? Requires
    a leading '<' plus a spec-bearing tag early on, so a stray '<' in prose (or a
    value like '<0.5 ms') doesn't trip it."""
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return False
    return bool(_HTML_HINT_RE.search(stripped[:5000]))


def _empty_response() -> SearxNGResponse:
    """A search response with no results — used as the pool's `merged` field on
    the URL/text paths, which don't search. The pipeline fallback reads infoboxes
    from it; empty means it contributes nothing, exactly as intended."""
    return SearxNGResponse(results=[], infoboxes=[], answers=[])


def _merge_boxes(responses: list[SearxNGResponse]) -> tuple[list, list]:
    infoboxes: list = []
    answers: list = []
    for resp in responses:
        if not infoboxes:
            infoboxes = resp.infoboxes
        if not answers:
            answers = resp.answers
    return infoboxes, answers


def _winning_provenance(
    candidates: list[ExtractionCandidate], value: str | None
) -> tuple[str | None, list[SourceResult]]:
    if value is None:
        return None, []
    matching = [c for c in candidates if c.value == value]
    if not matching:
        return None, []
    best = max(matching, key=lambda c: c.confidence)
    return best.source.url, [c.source for c in matching]


def _not_found(spec: AttributeSpec) -> ResolvedAttribute:
    return ResolvedAttribute(
        name=spec.name, type=spec.type, value=None, unit=None, raw_value=None,
        matched_allowed=None, confidence=0.0, source_url=None,
        status=ResolveStatus.NOT_FOUND, sources=[],
    )


def _match_boosted(conf: float, matched_allowed: bool | None) -> float:
    """Lift confidence when a value snapped exactly onto a caller-supplied
    allowed value. That agreement between the web value and the template's known
    vocabulary is strong corroboration, so a confirmed enum should read as
    reliable rather than sitting in the best-effort band."""
    if matched_allowed:
        return round(max(conf, 0.85), 4)
    return conf


def _coerce(
    spec: AttributeSpec, raw_value: str
) -> tuple[str, str | None, bool | None, float] | None:
    """Deterministically coerce a raw value to the requested type.

    Returns (value, unit, matched_allowed, confidence) or None to defer to the
    LLM normalizer (ambiguous unit conversion or an enum that didn't snap)."""
    allowed = spec.allowed_values
    value: str | None
    unit: str | None = None
    conf = 0.85

    if spec.type == AttrType.INTEGER:
        value = coerce_integer(select_segment(raw_value, spec.name))
        conf = 0.9
    elif spec.type == AttrType.NUMBER:
        res = coerce_number(select_segment(raw_value, spec.name), spec.unit)
        if res is None:
            return None
        value, unit, conf = res.value, res.unit, res.confidence
    elif spec.type == AttrType.BOOLEAN:
        value = coerce_boolean(raw_value)
        conf = 0.9
    elif spec.type == AttrType.ENUM or allowed:
        snapped, matched = snap_enum(raw_value, allowed or [])
        if not matched:
            return None  # let the LLM try a fuzzier match
        return snapped, None, True, 0.9
    else:  # STRING
        value, unit = clean_value(raw_value, spec.name)
        # A still-long string is usually an un-trimmed multi-spec blob, not a
        # clean value — drop confidence so the UI flags it for review instead of
        # presenting a paragraph as a reliable answer.
        if value and len(value) > 120:
            conf = min(conf, 0.4)
        elif value and len(value) > 80:
            conf = min(conf, 0.6)

    if value is None:
        return None

    # Snap a coerced numeric/string onto an allowed list when one is given
    # (e.g. number 180 → allowed ["60","120","180"]).
    if allowed:
        snapped, matched = snap_enum(value, allowed)
        if not matched:
            return None  # defer; LLM may convert/round to a listed value
        return snapped, unit or spec.unit, True, conf

    return value, unit or spec.unit, None, conf
