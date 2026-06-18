from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from app.core.config import settings
from app.domain.product import ProductQuery
from app.infrastructure.llm.ollama import OllamaGateway
from app.infrastructure.search.searxng import SearxNGClient

logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-\.]{1,60}\.[a-z]{2,10}$")

_SYSTEM_PROMPT = (
    "You are a product database expert. Given a product name or brand, "
    "respond ONLY with the manufacturer's official website domain "
    "(e.g. 'asus.com', 'samsung.com'). "
    "If you are unsure, respond with exactly 'unknown'. No explanation, no URL prefix."
)

# Generic words that are never brand names
_SKIP_TOKENS = {
    "the", "and", "for", "gaming", "series", "pro", "max", "plus", "mini",
    "ultra", "lite", "air", "new", "gen", "edition",
}


def _extract_ascii_brand(name: str) -> str:
    """Return ASCII brand+model tokens, skipping leading Cyrillic category words."""
    tokens = []
    for token in name.split():
        if not token.isascii():
            continue
        tokens.append(token)
        # Stop at first model-number-like token (e.g. G3424B, RTX4090)
        if re.match(r"^[A-Za-z]{0,3}[0-9]{3,}", token):
            break
    return " ".join(tokens)


def _looks_official(domain: str, brand_hint: str) -> bool:
    """Return True if any ASCII brand token appears in the domain."""
    ascii_tokens = [t.lower() for t in brand_hint.split() if t.isascii() and len(t) >= 2]
    if not ascii_tokens:
        return True  # fully Cyrillic — can't determine brand, allow any domain
    brand_tokens = [t for t in ascii_tokens if t not in _SKIP_TOKENS]
    if not brand_tokens:
        return True
    return any(t in domain for t in brand_tokens)


def _clean_domain(raw: str) -> str | None:
    raw = raw.lower().strip()
    raw = re.sub(r"^https?://", "", raw)
    raw = re.sub(r"^www\.", "", raw)
    raw = raw.split("/")[0].strip()
    return raw if _DOMAIN_RE.match(raw) else None


def _extract_domain(url: str) -> str | None:
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
        host = re.sub(r"^www\.", "", host)
        # Normalize to root domain (strip subdomains beyond 2 parts)
        # e.g. shop.2egaming.com → 2egaming.com
        # but keep two-part TLDs like co.uk: samsung.co.uk stays as-is
        parts = host.split(".")
        if len(parts) > 2:
            if len(parts[-2]) <= 3:
                host = ".".join(parts[-3:]) if len(parts) >= 3 else host
            else:
                host = ".".join(parts[-2:])
        return host if _DOMAIN_RE.match(host) else None
    except Exception:
        return None


def _brand_hint(product: ProductQuery) -> str:
    """Return the best brand string for domain lookup."""
    if product.brand:
        return product.brand
    # Fall back to extracting ASCII tokens from product name
    ascii_brand = _extract_ascii_brand(product.name)
    return ascii_brand if ascii_brand else product.name


class OfficialSiteResolver:
    def __init__(self, ollama: OllamaGateway, searxng: SearxNGClient):
        self._ollama = ollama
        self._searxng = searxng

    async def resolve(self, product: ProductQuery) -> str | None:
        hint = _brand_hint(product)
        domain = await self._resolve_via_llm(hint)
        if domain:
            logger.info("official domain via LLM: %s", domain)
            return domain

        domain = await self._resolve_via_search(product, hint)
        if domain:
            logger.info("official domain via search: %s", domain)
        return domain

    async def _resolve_via_llm(self, brand_hint: str) -> str | None:
        try:
            content = await self._ollama.chat(
                _SYSTEM_PROMPT, brand_hint,
                timeout=settings.query_builder_timeout_seconds,
            )
            domain = _clean_domain(content)
            return domain if domain and domain != "unknown" else None
        except Exception as exc:
            logger.debug("LLM domain resolution failed: %s", exc)
            return None

    async def _resolve_via_search(self, product: ProductQuery, brand_hint: str) -> str | None:
        # Prefer EAN/UPC for exact lookup, otherwise use brand hint
        if product.ean13:
            query = f'{product.ean13} official website'
        elif product.upc:
            query = f'{product.upc} official website'
        else:
            query = f'"{brand_hint}" official website'
        try:
            result = await self._searxng.search(query, num_results=8)
            for r in result.results:
                domain = _extract_domain(r.url)
                if domain and _looks_official(domain, brand_hint):
                    return domain
        except Exception as exc:
            logger.debug("search domain resolution failed: %s", exc)
        return None
