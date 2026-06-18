"""Rank candidate source URLs before fetching, instead of arbitrary insertion order."""
from __future__ import annotations

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
}

_OFFICIAL_BONUS = 20.0
_MULTI_HIT_BONUS = 1.5  # per extra query result that surfaced the same URL


def _domain_bonus(url: str) -> float:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, bonus in _QUALITY_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return bonus
    return 0.0


def rank_sources(
    responses: list[SearxNGResponse],
    official_domain: str | None = None,
) -> list[tuple[str, str]]:
    """Merge results across query responses and order them by relevance.

    Combines: SearxNG's own score, position within each result list, how many
    queries surfaced the URL, a static quality-domain bonus, and a large bonus
    for the manufacturer's official domain. Returns ordered (url, title) pairs.
    """
    agg: dict[str, dict] = {}
    for resp in responses:
        n = len(resp.results)
        for idx, r in enumerate(resp.results):
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
        total = e["pts"] + (e["hits"] - 1) * _MULTI_HIT_BONUS + _domain_bonus(url)
        if official_domain and url_matches_domain(url, official_domain):
            total += _OFFICIAL_BONUS
        ranked.append((total, url, e["title"]))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [(url, title) for _, url, title in ranked]
