import asyncio
import json
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_http_client = httpx.AsyncClient(timeout=settings.query_builder_timeout_seconds + 2)

_SYSTEM_PROMPT = (
    "You are a search query expert. Given a product name and an attribute the user "
    "wants to find, generate exactly 3 targeted web search queries that will most "
    "likely return spec pages, official documentation, or detailed reviews containing "
    "that attribute. Respond ONLY with a JSON array of 3 strings, no explanation."
)

_OFFICIAL_SYSTEM_PROMPT = (
    "You are a search query expert. Given a product name, an attribute, and the "
    "manufacturer's official website domain, generate exactly 3 search queries "
    "that use the 'site:' operator to search only on that official domain. "
    "Respond ONLY with a JSON array of 3 strings, no explanation."
)


def _fallback_queries(product: str, attribute: str, official_domain: str | None = None) -> list[str]:
    if official_domain:
        return [
            f'site:{official_domain} "{product}" {attribute}',
            f'site:{official_domain} {product} specifications {attribute}',
            f'site:{official_domain} {product} manual {attribute}',
        ]
    return [
        f'"{product}" {attribute} specifications',
        f'"{product}" {attribute} site:techpowerup.com OR site:gsmarena.com',
        f'"{product}" datasheet {attribute}',
    ]


async def build_queries(
    product: str,
    attribute: str,
    official_domain: str | None = None,
) -> list[str]:
    if official_domain:
        system = _OFFICIAL_SYSTEM_PROMPT
        user_content = (
            f'Product: "{product}"\n'
            f'Attribute: "{attribute}"\n'
            f'Official domain: {official_domain}'
        )
    else:
        system = _SYSTEM_PROMPT
        user_content = f'Product: "{product}"\nAttribute: "{attribute}"'

    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    }
    try:
        result = await asyncio.wait_for(
            _do_ollama_call(payload),
            timeout=settings.query_builder_timeout_seconds,
        )
        return result
    except Exception as exc:
        logger.warning("query_builder fallback: %s", exc)
        return _fallback_queries(product, attribute, official_domain)


async def _do_ollama_call(payload: dict) -> list[str]:
    resp = await _http_client.post(f"{settings.ollama_url}/api/chat", json=payload)
    resp.raise_for_status()
    content = resp.json()["message"]["content"].strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]

    queries = json.loads(content)
    if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
        return queries[:3]
    raise ValueError("Unexpected Ollama response format")
