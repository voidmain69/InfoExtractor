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


settings = Settings()
