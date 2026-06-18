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

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _is_model_token(t: str) -> bool:
    """A model-number-like token: contains both a letter and a digit (e.g. h610m-k, d4)."""
    return bool(re.search(r"[a-z]", t)) and bool(re.search(r"\d", t))


def match_score(product: ProductQuery, page: FetchedPage) -> float:
    title = (page.title or "").lower()
    html_lower = page.html.lower()

    # Exact identifier present on the page → near-certain match.
    for ident in (product.ean13, product.upc, product.mpn, product.article):
        if ident and ident.lower() in html_lower:
            return 1.0

    title_tokens = set(_tokens(title))
    query_tokens = set(_tokens(product.search_string()))

    # Coverage: fraction of the product-name tokens that appear in the title.
    name_tokens = [t for t in _tokens(product.name) if len(t) > 1]
    coverage = (
        sum(1 for t in name_tokens if t in title_tokens) / len(name_tokens)
        if name_tokens else 0.0
    )

    # Penalty: model-like tokens in the title that the query did not ask for
    # (this is what separates "H610M-K" from "H610M-K D4").
    extra_models = [t for t in title_tokens if _is_model_token(t) and t not in query_tokens]
    penalty = 0.25 * len(extra_models)

    fuzzy = difflib.SequenceMatcher(
        None, product.search_string().lower(), title, autojunk=False
    ).ratio()

    score = max(coverage - penalty, fuzzy * 0.6)
    return max(0.0, min(1.0, score))


def keep_relevant(product: ProductQuery, pages: list[FetchedPage]) -> list[FetchedPage]:
    """Drop pages about a different product; never return empty if any page exists."""
    relevant = [p for p in pages if match_score(product, p) >= MATCH_FLOOR]
    if relevant:
        return relevant
    if pages:
        return [max(pages, key=lambda p: match_score(product, p))]
    return []
