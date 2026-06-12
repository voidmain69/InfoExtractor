import asyncio
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from config import settings
from searxng_client import search as searxng_search

logger = logging.getLogger(__name__)

_http_client = httpx.AsyncClient(timeout=settings.query_builder_timeout_seconds + 2)

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-\.]{1,60}\.[a-z]{2,10}$")

_SYSTEM_PROMPT = (
    "You are a product database expert. Given a product name or brand, "
    "respond ONLY with the manufacturer's official website domain "
    "(e.g. 'asus.com', 'samsung.com'). "
    "If you are unsure, respond with exactly 'unknown'. No explanation, no URL prefix."
)


async def resolve_official_domain(product: str) -> str | None:
    domain = await _resolve_via_llm(product)
    if domain:
        logger.info("official domain via LLM: %s", domain)
        return domain

    domain = await _resolve_via_search(product)
    if domain:
        logger.info("official domain via search: %s", domain)
    return domain


async def _resolve_via_llm(product: str) -> str | None:
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": product},
        ],
        "stream": False,
    }
    try:
        resp = await asyncio.wait_for(
            _do_call(payload),
            timeout=settings.query_builder_timeout_seconds,
        )
        domain = _clean_domain(resp)
        return domain if domain and domain != "unknown" else None
    except Exception as exc:
        logger.debug("LLM domain resolution failed: %s", exc)
        return None


async def _resolve_via_search(product: str) -> str | None:
    query = f'"{product}" official website manufacturer'
    try:
        result = await searxng_search(query, num_results=5)
        for r in result.results:
            domain = _extract_domain(r.url)
            if domain and _looks_official(domain, product):
                return domain
    except Exception as exc:
        logger.debug("search domain resolution failed: %s", exc)
    return None


async def _do_call(payload: dict) -> str:
    resp = await _http_client.post(f"{settings.ollama_url}/api/chat", json=payload)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def _clean_domain(raw: str) -> str | None:
    # Strip common prefixes the model might add despite instructions
    raw = raw.lower().strip()
    raw = re.sub(r"^https?://", "", raw)
    raw = re.sub(r"^www\.", "", raw)
    raw = raw.split("/")[0].strip()
    return raw if _DOMAIN_RE.match(raw) else None


def _extract_domain(url: str) -> str | None:
    try:
        host = urlparse(url).hostname or ""
        host = re.sub(r"^www\.", "", host.lower())
        return host if _DOMAIN_RE.match(host) else None
    except Exception:
        return None


def _looks_official(domain: str, product: str) -> bool:
    """Heuristic: first token of product name should appear in domain."""
    brand = product.split()[0].lower()
    # Allow short brands (e.g. LG, HP)
    if len(brand) <= 2:
        return True
    return brand in domain
