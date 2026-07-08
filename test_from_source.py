"""Network-free tests for the from-url / from-text typed-resolve paths.

The LLM stages (semantic matcher, value normalizer) and the page fetchers are
mocked; the deterministic core (spec pooling, fuzzy label match, coercion, unit
handling, enum snapping, provenance) runs for real. Run:

    pip install -r requirements.txt -r requirements-dev.txt
    pytest test_from_source.py -q
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.attributes import AttributeSpec, AttrType, ResolveStatus
from app.domain.page import FetchedPage
from app.infrastructure.cache.ttl_cache import TTLCacheStore
from app.services.attribute_matcher import text_pool
from app.services.resolve_service import ResolveService

_SPEC_HTML = """
<html><body>
  <section class="specs">
    <h2>Specifications</h2>
    <table class="spec-table">
      <tr><td>Refresh rate</td><td>180 Hz</td></tr>
      <tr><td>Panel type</td><td>VA</td></tr>
      <tr><td>Response time</td><td>1 ms</td></tr>
    </table>
  </section>
</body></html>
"""

_ATTRS = [
    AttributeSpec(name="Refresh rate", type=AttrType.NUMBER, unit="Hz",
                  allowed_values=["60", "120", "144", "165", "180"]),
    AttributeSpec(name="Panel type", type=AttrType.ENUM,
                  allowed_values=["IPS", "VA", "TN", "OLED"]),
]


def _service(fetch_pages, fetch_with_js) -> ResolveService:
    """ResolveService with the LLM/search collaborators mocked; only the
    deterministic pool→coerce pipeline is exercised."""
    semantic = MagicMock()
    semantic.match = AsyncMock(side_effect=lambda names, labels: [None] * len(names))
    normalizer = MagicMock()
    normalizer.normalize = AsyncMock(side_effect=lambda items: [])  # coerce handles all
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    return ResolveService(
        searxng=MagicMock(),
        query_builder=MagicMock(),
        official_site=MagicMock(),
        pipeline=pipeline,
        normalizer=normalizer,
        semantic_matcher=semantic,
        cache=TTLCacheStore(100, 60),
        fetch_pages=fetch_pages,
        fetch_with_js=fetch_with_js,
    )


# ── text_pool ────────────────────────────────────────────────────────────────

def test_text_pool_colon_lines():
    specs = text_pool("Refresh rate: 180 Hz\nPanel type: VA\n")
    got = {(s.name, s.value) for s in specs}
    assert got == {("Refresh rate", "180 Hz"), ("Panel type", "VA")}


def test_text_pool_pipe_split_and_dedup():
    # The xlsx flattener joins a row's cells with " | ".
    specs = text_pool("Потужність: 2100 Вт | Вага: 5.2 кг\nПотужність: 2100 Вт\n")
    got = [(s.name, s.value) for s in specs]
    assert ("Потужність", "2100 Вт") in got
    assert ("Вага", "5.2 кг") in got
    assert len(got) == 2  # duplicate "Потужність" line dropped


def test_text_pool_ignores_non_pairs():
    assert text_pool("just a sentence with no colon\n") == []


# ── resolve_from_url ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_from_url_types_and_snaps():
    page = FetchedPage(url="https://x/p", title="", html=_SPEC_HTML, text="", status_code=200)
    fetch_pages = AsyncMock(return_value=[page])
    fetch_with_js = AsyncMock(return_value=None)  # static pool is enough
    svc = _service(fetch_pages, fetch_with_js)

    resp = await svc.resolve_from_url("https://x/p", _ATTRS)
    by = {r.name: r for r in resp.results}

    assert by["Refresh rate"].status == ResolveStatus.FOUND
    assert by["Refresh rate"].value == "180"
    assert by["Refresh rate"].unit == "Hz"
    assert by["Refresh rate"].matched_allowed is True
    assert by["Refresh rate"].source_url == "https://x/p"

    assert by["Panel type"].status == ResolveStatus.FOUND
    assert by["Panel type"].value == "VA"
    assert by["Panel type"].matched_allowed is True

    fetch_pages.assert_awaited_once()  # single fetch, no search sweep


@pytest.mark.asyncio
async def test_resolve_from_url_missing_attr_not_found():
    page = FetchedPage(url="https://x/p", title="", html=_SPEC_HTML, text="", status_code=200)
    svc = _service(AsyncMock(return_value=[page]), AsyncMock(return_value=None))

    absent = [AttributeSpec(name="Bluetooth version", type=AttrType.STRING)]
    resp = await svc.resolve_from_url("https://x/p", absent)
    assert resp.results[0].status == ResolveStatus.NOT_FOUND
    assert resp.results[0].value is None


# ── resolve_from_text ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_from_text_types_and_snaps():
    # No page fetch on the text path.
    fetch_pages = AsyncMock(side_effect=AssertionError("from-text must not fetch"))
    svc = _service(fetch_pages, AsyncMock(return_value=None))

    text = "Refresh rate: 180 Hz\nPanel type: VA\n"
    resp = await svc.resolve_from_text(text, _ATTRS)
    by = {r.name: r for r in resp.results}

    assert by["Refresh rate"].value == "180"
    assert by["Refresh rate"].unit == "Hz"
    assert by["Panel type"].value == "VA"
    fetch_pages.assert_not_awaited()
