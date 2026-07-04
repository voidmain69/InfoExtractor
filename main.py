import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.middleware import APIKeyMiddleware, RateLimitMiddleware
from app.api.routes import attribute, resolve, specs, system
from app.core.config import settings
from app.core.logging import configure_logging
from app.extraction.pipeline import ExtractionPipeline
from app.infrastructure.cache.ttl_cache import TTLCacheStore
from app.infrastructure.fetch.browser_fetcher import fetch_with_js
from app.infrastructure.fetch.http_fetcher import fetch_pages
from app.infrastructure.llm.ollama import OllamaGateway
from app.infrastructure.query.query_builder import QueryBuilder
from app.infrastructure.search.searxng import SearxNGClient
from app.services.attribute_service import AttributeService
from app.services.official_site import OfficialSiteResolver
from app.services.resolve_service import ResolveService
from app.services.semantic_matcher import SemanticMatcher
from app.services.specs_service import SpecsService
from app.services.value_normalizer import ValueNormalizer

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared HTTP client for Ollama + SearxNG (generous timeout covers LLM extraction)
    client = httpx.AsyncClient(timeout=settings.llm_extraction_timeout_seconds + 5)

    ollama = OllamaGateway(
        client, settings.ollama_url, settings.ollama_model,
        max_concurrency=settings.ollama_max_concurrency,
        keep_alive=settings.ollama_keep_alive,
    )
    # Fire-and-forget: pull the model into memory now, not inside the first
    # request's 6–30s stage budgets (a CPU-only host cold-loads for minutes).
    warmup_task = asyncio.create_task(ollama.warmup())
    searxng = SearxNGClient(client, settings.searxng_url)
    query_builder = QueryBuilder(ollama)
    official_site = OfficialSiteResolver(ollama, searxng)
    pipeline = ExtractionPipeline(ollama)
    normalizer = ValueNormalizer(ollama)
    semantic_matcher = SemanticMatcher(ollama)
    cache = TTLCacheStore(settings.cache_max_size, settings.cache_ttl_seconds)

    app.state.searxng = searxng
    app.state.attribute_service = AttributeService(
        searxng, query_builder, official_site, pipeline, cache, fetch_pages, fetch_with_js
    )
    app.state.specs_service = SpecsService(
        searxng, query_builder, official_site, fetch_pages, fetch_with_js
    )
    app.state.resolve_service = ResolveService(
        searxng, query_builder, official_site, pipeline, normalizer,
        semantic_matcher, cache, fetch_pages, fetch_with_js
    )

    try:
        yield
    finally:
        warmup_task.cancel()
        await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="getAttrService", version="3.0.0", lifespan=lifespan)
    # add_middleware stacks LIFO: the last added runs first. Add auth first and
    # the rate limiter last so abusive callers are shed before the auth check.
    app.add_middleware(APIKeyMiddleware, api_key=settings.api_key)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    app.include_router(system.router)
    app.include_router(attribute.router)
    app.include_router(specs.router)
    app.include_router(resolve.router)
    return app


app = create_app()
