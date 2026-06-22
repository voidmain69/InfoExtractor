from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    searxng_url: str = "http://localhost:8080"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"

    cache_ttl_seconds: int = 3600
    cache_max_size: int = 2000

    max_concurrent_fetches: int = 5
    page_fetch_timeout_seconds: float = 8.0
    max_sources: int = 5

    query_builder_timeout_seconds: float = 6.0
    llm_extraction_timeout_seconds: float = 60.0

    use_playwright: bool = True
    playwright_timeout_seconds: float = 30.0

    # Protects the single Ollama model server from concurrent batch requests.
    # 0 disables the limit.
    ollama_max_concurrency: int = 4

    # Batch /attributes resolution tuning.
    resolve_max_concurrency: int = 4
    resolve_targeted_fallback: bool = True
    resolve_match_threshold: float = 0.78  # spec-pool fuzzy name match
    normalize_timeout_seconds: float = 30.0

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

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


settings = Settings()
