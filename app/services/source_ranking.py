"""Rank candidate source URLs before fetching, instead of arbitrary insertion order."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.domain.page import SearxNGResponse
from app.services.url_filter import url_matches_domain

# Domains known to host high-quality structured specifications.
_QUALITY_DOMAINS: dict[str, float] = {
    "techpowerup.com": 5.0,
    "gsmarena.com": 5.0,
    "notebookcheck.net": 4.0,
    "anandtech.com": 4.0,
    "rtings.com": 4.0,
    "displayspecifications.com": 4.0,
    "kimovil.com": 3.0,
    "versus.com": 2.0,
    # UA retail/aggregators with consistently structured spec tables.
    "rozetka.com.ua": 3.5,
    "hotline.ua": 3.0,
    "ek.ua": 3.0,
    "e-katalog.ru": 2.0,
    "comfy.ua": 2.5,
    "moyo.ua": 2.5,
    "foxtrot.com.ua": 2.5,
    "allo.ua": 2.0,
    "eldorado.ua": 2.0,
}

# Domains that essentially never carry extractable spec tables.
_JUNK_DOMAINS: dict[str, float] = {
    "youtube.com": -25.0,
    "facebook.com": -25.0,
    "instagram.com": -25.0,
    "tiktok.com": -25.0,
    "pinterest.com": -20.0,
    "x.com": -20.0,
    "twitter.com": -20.0,
    "reddit.com": -10.0,
    "quora.com": -10.0,
    "aliexpress.com": -6.0,
    "ebay.com": -6.0,
    "olx.ua": -12.0,
}

# The fetcher parses HTML only; binary documents waste a fetch slot.
_SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".zip",
                    ".rar", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".exe")

_OFFICIAL_BONUS = 20.0
_BRAND_BONUS = 12.0     # manufacturer's own domain (even without official_only)
_MULTI_HIT_BONUS = 1.5  # per extra query result that surfaced the same URL
_MODEL_IN_URL_BONUS = 8.0  # URL slug contains the model number → product page,
                           # not the brand homepage / category listing

_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _model_in_url(url: str, hints: list[str]) -> bool:
    slug = _ALNUM_RE.sub("", url.lower())
    for hint in hints:
        h = _ALNUM_RE.sub("", hint.lower())
        if len(h) >= 4 and h in slug:
            return True
    return False

# Global manufacturer sites serve dozens of locales; prefer UA/EN page variants
# over an arbitrary regional one (extraction labels then match the taxonomy).
# Sites disagree on order (en-us vs ua-uk), so either half counts.
_LOCALE_PAIR_RE = re.compile(r"/([a-z]{2})[-_]([a-z]{2})(?=/|$)", re.I)
_LOCALE_SINGLE_RE = re.compile(r"/(?:ua|uk|en)(?=/|$)|[?&]lang=(?:en|uk|ua)", re.I)
_PREFERRED_LANGS = {"en", "uk", "ua"}
_LOCALE_BONUS = 4.0

# Support/service/download sections of manufacturer sites outrank the real
# product page for exact-model queries but carry no spec tables.
_SUPPORT_PATH_RE = re.compile(
    r"supportdetail|productservice|product-service|/servis|/service[s]?/|"
    r"/get-help|/customer|/warranty|/repair|/manual|/download|/instruction|"
    r"/spares|/faq|/register|/where-to-buy|/store-locator",
    re.I,
)
_SUPPORT_PENALTY = 12.0


def _locale_adjust(url: str) -> float:
    path = url.split("://", 1)[-1]
    path = path[path.find("/"):] if "/" in path else ""
    m = _LOCALE_PAIR_RE.search(path)
    if m:
        if {m.group(1).lower(), m.group(2).lower()} & _PREFERRED_LANGS:
            return _LOCALE_BONUS
        return -_LOCALE_BONUS
    if _LOCALE_SINGLE_RE.search(url):
        return _LOCALE_BONUS
    return 0.0


def _domain_score(url: str) -> float:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for table in (_QUALITY_DOMAINS, _JUNK_DOMAINS):
        for domain, bonus in table.items():
            if host == domain or host.endswith("." + domain):
                return bonus
    return 0.0


def rank_sources(
    responses: list[SearxNGResponse],
    official_domain: str | None = None,
    brand_domains: list[str] | None = None,
    model_hints: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Merge results across query responses and order them by relevance.

    Combines: SearxNG's own score, position within each result list, how many
    queries surfaced the URL, a static quality/junk-domain adjustment, a bonus
    for the manufacturer's own domains, and a large bonus for the resolved
    official domain. Returns ordered (url, title) pairs.
    """
    agg: dict[str, dict] = {}
    for resp in responses:
        n = len(resp.results)
        for idx, r in enumerate(resp.results):
            low = r.url.lower().split("?")[0]
            if low.endswith(_SKIP_EXTENSIONS):
                continue
            position_pts = float(n - idx)  # earlier results rank higher
            score_pts = r.score or 0.0
            entry = agg.get(r.url)
            if entry is None:
                agg[r.url] = {"title": r.title, "pts": position_pts + score_pts, "hits": 1}
            else:
                entry["pts"] += position_pts + score_pts
                entry["hits"] += 1
                if not entry["title"]:
                    entry["title"] = r.title

    ranked: list[tuple[float, str, str]] = []
    for url, e in agg.items():
        total = e["pts"] + (e["hits"] - 1) * _MULTI_HIT_BONUS + _domain_score(url)
        if official_domain and url_matches_domain(url, official_domain):
            total += _OFFICIAL_BONUS
        if brand_domains and any(url_matches_domain(url, d) for d in brand_domains):
            total += _BRAND_BONUS
        if model_hints and _model_in_url(url, model_hints):
            total += _MODEL_IN_URL_BONUS
        total += _locale_adjust(url)
        if _SUPPORT_PATH_RE.search(url):
            total -= _SUPPORT_PENALTY
        ranked.append((total, url, e["title"]))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [(url, title) for _, url, title in ranked]
