from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    searxng_url: str = "http://localhost:8080"
    ollama_url: str = "http://localhost:11434"
    # Aligned with catalog-service so the shared GPU host keeps ONE model
    # resident in VRAM instead of swapping between the two services' models.
    ollama_model: str = "qwen2.5:14b-instruct"
    # How long Ollama keeps the model resident after a call (Ollama's own
    # default is a mere 5m). On a CPU-only host a cold load takes minutes —
    # longer than every per-stage timeout — so an evicted model turns the first
    # request after an idle gap into a silent all-stages-degraded one.
    ollama_keep_alive: str = "2h"

    cache_ttl_seconds: int = 3600
    cache_max_size: int = 2000

    max_concurrent_fetches: int = 5
    page_fetch_timeout_seconds: float = 12.0
    max_sources: int = 5

    query_builder_timeout_seconds: float = 6.0
    llm_extraction_timeout_seconds: float = 60.0

    use_playwright: bool = True
    playwright_timeout_seconds: float = 30.0
    # Cap concurrent headless Chromium instances — each launch is heavy, so an
    # unbounded burst of /specs requests could otherwise exhaust host memory.
    playwright_max_concurrency: int = 2
    # /specs JS fallback: trigger a headless render whenever the best static
    # score is below this (higher = lower barrier, Playwright runs more often),
    # and render up to this many of the top URLs (not just one) to merge specs
    # revealed behind tabs/accordions/"show more".
    playwright_score_threshold: int = 25
    playwright_max_urls: int = 2

    # Protects the single Ollama model server from concurrent batch requests.
    # 0 disables the limit.
    ollama_max_concurrency: int = 4

    # Batch /attributes resolution tuning.
    resolve_max_concurrency: int = 4
    resolve_targeted_fallback: bool = True
    resolve_match_threshold: float = 0.78  # spec-pool fuzzy name match
    normalize_timeout_seconds: float = 30.0

    # TEI (Text Embeddings Inference) for the semantic attribute→label match.
    # An empty tei_dense_url disables the vector tier entirely: SemanticMatcher
    # then behaves exactly as before (curated synonyms upstream + the LLM tail).
    # rerank_url is optional — set it to add a cross-encoder reorder over the
    # dense candidates. Mirrors catalog-service's bge-m3 / bge-reranker-v2-m3.
    tei_dense_url: str = ""
    tei_rerank_url: str = ""
    tei_timeout_seconds: float = 10.0
    # Accept a dense match at/above this cosine and only when it beats the
    # runner-up by the margin — else defer to the LLM (mirrors the catalog
    # import key-mapping ANN tier: 0.72 / 0.04).
    semantic_ann_accept: float = 0.72
    semantic_ann_margin: float = 0.04

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    # Security: API key required in the X-API-Key header. Empty disables auth
    # (dev only — the service is then an open SSRF/DoS proxy if exposed).
    api_key: str = ""

    # Edge rate limit: max requests per client IP per window. 0 disables it.
    rate_limit_requests: int = 60
    rate_limit_window_seconds: float = 60.0

    # Hard cap on a fetched page body (bytes) — guards against memory-exhaustion
    # from a hostile URL returning a huge response.
    max_page_bytes: int = 3_000_000

    # SearxNG content filter level: 0=off, 1=moderate, 2=strict.
    searxng_safesearch: int = 1

    # Anti-blocking: retry behaviour for HTTP fetcher
    fetch_retry_attempts: int = 3       # max retries on 429/503/403
    fetch_retry_backoff: float = 1.0    # base backoff seconds (doubles per retry)
    fetch_jitter_max: float = 0.4       # max random sleep before each request

    # Anti-blocking: comma-separated proxy URLs, e.g.
    #   http://user:pass@proxy1:3128,http://user:pass@proxy2:3128
    # Leave empty to disable.
    proxy_list: str = ""

    # SearxNG anti-throttle: cache identical search responses and pace outgoing
    # queries so a single batch request can't burst the upstream engines into
    # CAPTCHA/rate-limit territory.
    searxng_cache_ttl_seconds: int = 1800
    searxng_cache_max_size: int = 1000
    # Persist search responses to this JSON file (survives restarts/rebuilds),
    # so repeated identical queries never re-hit the upstream engines. Empty
    # disables the disk layer. Point it into a mounted volume in docker.
    searxng_cache_file: str = ""
    searxng_min_interval_seconds: float = 0.34  # min spacing between queries
    searxng_max_concurrency: int = 2            # parallel queries cap
    searxng_retry_attempts: int = 2             # retry empty/failed responses
    searxng_retry_backoff: float = 0.8

    # /attributes pool shaping: cap the merged spec pool to the most relevant
    # pages (drops vendor-list/download noise) and skip the per-attribute web
    # fallback when the shared pool is already rich — that fallback is the main
    # self-inflicted query burst that re-triggers engine throttling.
    resolve_pool_max_pages: int = 3
    resolve_pool_rich_threshold: int = 40
    # When the static pool ends up sparser than this, JS-render the top URLs
    # (manufacturer pages are frequently client-rendered or hide specs behind
    # "show all") and merge whatever the browser reveals.
    resolve_pool_js_threshold: int = 30


settings = Settings()
