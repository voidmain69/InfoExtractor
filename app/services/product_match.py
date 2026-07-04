"""Verify that a fetched page is actually about the queried product.

Guards against variant contamination (e.g. a search for "H610M-K" returning
"H610M-K D4" pages) and against entirely unrelated results. Returns a 0..1
weight used both to drop clearly-wrong pages and to scale candidate confidence.
"""
from __future__ import annotations

import difflib
import re

from app.domain.page import FetchedPage
from app.domain.product import ProductQuery

# Floor below which a page is considered not about the product.
MATCH_FLOOR = 0.30
# Absolute floor for the never-return-empty fallback: below this the "best"
# page is a category listing or unrelated article — extracting from it yields
# filter facets and foreign specs, which is worse than returning nothing.
FALLBACK_FLOOR = 0.12

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _is_model_token(t: str) -> bool:
    """A model-number-like token: contains both a letter and a digit (e.g. h610m-k, d4)."""
    return bool(re.search(r"[a-z]", t)) and bool(re.search(r"\d", t))


def _fragment_of_any(token_alnum: str, query_alnum: list[str]) -> bool:
    """True when the token and some queried token contain one another
    (alphanumeric-normalized) — i.e. it's the same identifier respelled."""
    if len(token_alnum) < 3:
        return False
    return any(
        len(q) >= 3 and (token_alnum in q or q in token_alnum)
        for q in query_alnum
    )


def match_score(product: ProductQuery, page: FetchedPage) -> float:
    title = (page.title or "").lower()
    html_lower = page.html.lower()

    # Exact identifier present on the page → near-certain match.
    for ident in (product.ean13, product.upc, product.mpn, product.article):
        if ident and ident.lower() in html_lower:
            return 1.0

    title_tokens = set(_tokens(title))
    query_tokens = set(_tokens(product.search_string()))

    # Coverage: fraction of the product-name tokens that appear in the title,
    # counting respellings ("L2352DW" / "WAN 28280" for HL-L2352DW/WAN28280UA):
    # a name token is covered when it and a title token contain one another
    # after alphanumeric normalization.
    name_tokens = [t for t in _tokens(product.name) if len(t) > 1]
    title_alnum = [re.sub(r"[^a-z0-9]", "", t) for t in title_tokens]
    title_alnum = [t for t in title_alnum if len(t) >= 4]

    def _covered(t: str) -> bool:
        if t in title_tokens:
            return True
        ta = re.sub(r"[^a-z0-9]", "", t)
        return len(ta) >= 4 and any(ta in x or x in ta for x in title_alnum)

    coverage = (
        sum(1 for t in name_tokens if _covered(t)) / len(name_tokens)
        if name_tokens else 0.0
    )

    # Penalty: model-like tokens in the title that the query did not ask for
    # (this is what separates "H610M-K" from "H610M-K D4"). Applied AFTER the
    # coverage/fuzzy max — sibling model numbers (G2423B vs G3424B) are close
    # enough that char-level fuzzy alone would wave the wrong product through.
    # A token that is a spelling fragment of a queried model (L2352DW inside
    # HL-L2352DW) is the SAME product written differently, not a sibling.
    query_alnum = [re.sub(r"[^a-z0-9]", "", t) for t in query_tokens]
    extra_models = [
        t for t in title_tokens
        if _is_model_token(t) and t not in query_tokens
        and not _fragment_of_any(re.sub(r"[^a-z0-9]", "", t), query_alnum)
    ]
    penalty = 0.25 * len(extra_models)

    fuzzy = difflib.SequenceMatcher(
        None, product.search_string().lower(), title, autojunk=False
    ).ratio()

    score = max(coverage, fuzzy * 0.6) - penalty

    # Digit-run gate: word-token coverage and fuzzy both wave sibling models
    # through when they differ only by a digit ("K 5" vs "K 2 Power Control").
    # When BOTH the model name and the title carry digit runs but share none,
    # the page is about a different model — cap it below the relevance floor.
    # (A digit-less marketing title is no evidence either way — left alone.)
    name_runs = set(re.findall(r"\d+", product.name))
    title_runs = set(re.findall(r"\d+", title))
    if name_runs and title_runs and not (name_runs & title_runs):
        score = min(score, 0.25)

    return max(0.0, min(1.0, score))


def keep_relevant(
    product: ProductQuery,
    pages: list[FetchedPage],
    top_n: int | None = None,
) -> list[FetchedPage]:
    """Drop pages about a different product; never return empty if any page exists.

    When `top_n` is given, keep only the highest-scoring pages — this trims noisy
    extras (vendor listings, download pages) that would otherwise pollute a merged
    spec pool and degrade attribute matching."""
    scored = [(match_score(product, p), p) for p in pages]
    relevant = [(s, p) for s, p in scored if s >= MATCH_FLOOR]
    if not relevant and scored:
        best = max(scored, key=lambda sp: sp[0])
        relevant = [best] if best[0] >= FALLBACK_FLOOR else []
    relevant.sort(key=lambda sp: sp[0], reverse=True)
    if top_n is not None:
        relevant = relevant[:top_n]
    return [p for _, p in relevant]
