"""Batch attribute resolution: fetch a product's pages once, resolve many typed
attributes against that shared content, then normalize values with the AI layer."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.core.config import settings
from app.domain.attributes import AttributeSpec, AttrType, ResolveStatus, ResolvedAttribute
from app.domain.extraction import ExtractionCandidate, SourceResult
from app.domain.page import FetchedPage, SearxNGResponse
from app.domain.product import ProductQuery
from app.domain.responses import ResolveResponse
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.reconciler import reconcile
from app.extraction.value_cleaner import clean_value
from app.infrastructure.cache.ttl_cache import TTLCacheStore, make_key
from app.infrastructure.query.query_builder import QueryBuilder
from app.infrastructure.search.searxng import SearxNGClient
from app.services.attribute_matcher import (
    PooledSpec,
    build_spec_pool,
    candidates_for_label,
    match_in_pool,
    pool_labels,
)
from app.services.official_site import OfficialSiteResolver
from app.services.product_match import keep_relevant
from app.services.semantic_matcher import SemanticMatcher
from app.services.source_ranking import rank_sources
from app.services.url_filter import url_matches_domain
from app.services.value_normalizer import NormItem, ValueNormalizer

logger = logging.getLogger(__name__)

FetchPages = Callable[[list[str], dict[str, str]], Awaitable[list[FetchedPage]]]


@dataclass
class _Pool:
    pages: list[FetchedPage]
    merged: SearxNGResponse
    specs: list[PooledSpec]


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
    ):
        self._searxng = searxng
        self._query_builder = query_builder
        self._official_site = official_site
        self._pipeline = pipeline
        self._normalizer = normalizer
        self._semantic_matcher = semantic_matcher
        self._cache = cache
        self._fetch_pages = fetch_pages
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

            # 2a. One semantic-match call maps every requested attribute onto the
            # page's actual labels by meaning (synonyms / translations).
            semantic_labels: list[str | None] = [None] * len(misses)
            if pool.specs:
                semantic_labels = await self._semantic_matcher.match(
                    [spec.name for _, spec in misses], pool_labels(pool.specs)
                )

            # 2b. Resolve raw values for all misses (bounded concurrency).
            raw_list = await asyncio.gather(
                *[self._resolve_raw(product, spec, pool, label, official_only, max_sources)
                  for (_, spec), label in zip(misses, semantic_labels)]
            )

            # 3. Normalize (single batched AI call) + assemble.
            resolved = await self._normalize_all([s for _, s in misses], raw_list)
            for (i, spec), res in zip(misses, resolved):
                results[i] = res
                if res.status == ResolveStatus.FOUND:
                    self._cache.set(_cache_key(product, spec, max_sources, official_only), res)

        ordered = [results[i] for i in range(len(attributes))]
        return ResolveResponse(product=product, results=ordered, cached=not misses)

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
        responses = await asyncio.gather(*[self._searxng.search(q, num_results=8) for q in queries])
        ranked = rank_sources(responses, official_domain if official_only else None)
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

        pool_entries = build_spec_pool(pages)
        logger.info("/attributes spec pool: %d entries", len(pool_entries))

        infoboxes, answers = _merge_boxes(responses)
        merged = SearxNGResponse(
            results=list(responses[0].results) if responses else [],
            infoboxes=infoboxes,
            answers=answers,
        )
        return _Pool(pages=pages, merged=merged, specs=pool_entries)

    # ── per-attribute raw extraction ─────────────────────────────────────

    async def _resolve_raw(
        self,
        product: ProductQuery,
        spec: AttributeSpec,
        pool: _Pool,
        semantic_label: str | None,
        official_only: bool,
        max_sources: int,
    ) -> _Raw:
        async with self._sem:
            # Cheap path: string-fuzzy match against the already-extracted pool …
            candidates = match_in_pool(spec.name, pool.specs, settings.resolve_match_threshold)
            # … plus the semantic match (catches synonyms / translations).
            if semantic_label:
                candidates += candidates_for_label(pool.specs, semantic_label)
            candidates = _dedup(candidates)

            # Pipeline path on already-fetched pages (no re-fetch).
            if not candidates and pool.pages:
                candidates = await self._pipeline.run(product, spec.name, pool.merged, pool.pages)

            # Targeted per-attribute fallback (own small search).
            if not candidates and settings.resolve_targeted_fallback and not official_only:
                candidates = await self._targeted(product, spec.name, max_sources)

        value, _unit, confidence, _all = reconcile(candidates)
        source_url, sources = _winning_provenance(candidates, value)
        return _Raw(raw_value=value, confidence=confidence, source_url=source_url, sources=sources)

    async def _targeted(
        self, product: ProductQuery, attribute: str, max_sources: int
    ) -> list[ExtractionCandidate]:
        try:
            queries = await self._query_builder.build_queries(product, attribute)
            responses = await asyncio.gather(*[self._searxng.search(q, num_results=8) for q in queries])
            ranked = rank_sources(responses, None)
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
            # Deterministic shortcut: plain string with no unit / allowed values.
            if spec.type == AttrType.STRING and not spec.unit and not spec.allowed_values:
                value, derived_unit = clean_value(raw.raw_value, spec.name)
                results[idx] = ResolvedAttribute(
                    name=spec.name, type=spec.type, value=value, unit=derived_unit,
                    raw_value=raw.raw_value, matched_allowed=None,
                    confidence=raw.confidence, source_url=raw.source_url,
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
