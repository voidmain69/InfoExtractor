from __future__ import annotations

import logging

from app.core.config import settings
from app.domain.product import ProductQuery
from app.infrastructure.llm.ollama import OllamaGateway

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a search query expert. Given structured product info and an attribute the user "
    "wants to find, generate exactly 3 targeted web search queries that will most "
    "likely return spec pages, official documentation, or detailed reviews containing "
    "that attribute. Respond ONLY with a JSON array of 3 strings, no explanation."
)

_OFFICIAL_SYSTEM_PROMPT = (
    "You are a search query expert. Given structured product info, an attribute, and the "
    "manufacturer's official website domain, generate exactly 3 search queries "
    "that use the 'site:' operator to search only on that official domain. "
    "Respond ONLY with a JSON array of 3 strings, no explanation."
)

_SPECS_SYSTEM_PROMPT = (
    "You are a search query expert. Given structured product info, "
    "generate exactly 2 targeted web search queries that will find pages "
    "containing the COMPLETE product specifications or full tech specs list. "
    "Prioritize official manufacturer sites and dedicated spec databases. "
    "Respond ONLY with a JSON array of 2 strings, no explanation."
)


def _product_context(product: ProductQuery) -> str:
    """Build a structured product context string for LLM prompts."""
    lines = [f'Product name: "{product.name}"']
    if product.brand:
        lines.append(f'Brand: "{product.brand}"')
    if product.category:
        lines.append(f'Category: "{product.category}"')
    if product.article:
        lines.append(f'Article/SKU: "{product.article}"')
    if product.ean13:
        lines.append(f'EAN-13: {product.ean13}')
    if product.upc:
        lines.append(f'UPC: {product.upc}')
    return "\n".join(lines)


class QueryBuilder:
    def __init__(self, ollama: OllamaGateway):
        self._ollama = ollama

    async def build_queries(
        self,
        product: ProductQuery,
        attribute: str,
        official_domain: str | None = None,
    ) -> list[str]:
        product_ctx = _product_context(product)
        if official_domain:
            system = _OFFICIAL_SYSTEM_PROMPT
            user_content = (
                f"{product_ctx}\n"
                f'Attribute: "{attribute}"\n'
                f'Official domain: {official_domain}'
            )
        else:
            system = _SYSTEM_PROMPT
            user_content = f'{product_ctx}\nAttribute: "{attribute}"'

        try:
            result = await self._ollama.chat_json(
                system, user_content, timeout=settings.query_builder_timeout_seconds
            )
            return _validate(result)[:3]
        except Exception as exc:
            logger.warning("query_builder fallback: %s", exc)
            return _fallback_queries(product, attribute, official_domain)

    async def build_specs_queries(
        self,
        product: ProductQuery,
        official_domain: str | None = None,
    ) -> list[str]:
        product_ctx = _product_context(product)
        extra = f"\nOfficial domain: {official_domain}" if official_domain else ""
        try:
            result = await self._ollama.chat_json(
                _SPECS_SYSTEM_PROMPT, product_ctx + extra,
                timeout=settings.query_builder_timeout_seconds,
            )
            return _validate(result)[:2]
        except Exception as exc:
            logger.warning("build_specs_queries fallback: %s", exc)
            return _fallback_specs_queries(product, official_domain)


def _validate(queries) -> list[str]:
    if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
        return queries
    raise ValueError("Unexpected Ollama response format")


def _fallback_queries(product: ProductQuery, attribute: str, official_domain: str | None = None) -> list[str]:
    search = product.search_string()
    if official_domain:
        return [
            f'site:{official_domain} "{search}" {attribute}',
            f'site:{official_domain} {search} specifications {attribute}',
            f'site:{official_domain} {search} manual {attribute}',
        ]
    barcode = product.ean13 or product.upc
    if barcode:
        return [
            f'"{barcode}" {attribute} specifications',
            f'"{search}" {attribute} specifications',
            f'"{search}" {attribute} site:techpowerup.com OR site:gsmarena.com',
        ]
    return [
        f'"{search}" {attribute} specifications',
        f'"{search}" {attribute} site:techpowerup.com OR site:gsmarena.com',
        f'"{search}" datasheet {attribute}',
    ]


def _fallback_specs_queries(product: ProductQuery, official_domain: str | None) -> list[str]:
    search = product.search_string()
    if official_domain:
        return [
            f'site:{official_domain} "{search}" specifications',
            f'site:{official_domain} {search} technical specifications',
        ]
    barcode = product.ean13 or product.upc
    if barcode:
        return [
            f'"{barcode}" full specifications',
            f'"{search}" complete specifications',
        ]
    return [
        f'"{search}" complete specifications',
        f'"{search}" full specifications',
    ]
