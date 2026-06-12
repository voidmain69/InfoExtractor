import threading

from cachetools import TTLCache

from config import settings
from models import AttributeResponse, ProductQuery

_cache: TTLCache = TTLCache(
    maxsize=settings.cache_max_size,
    ttl=settings.cache_ttl_seconds,
)
_lock = threading.Lock()


def make_key(
    product: ProductQuery,
    attribute: str,
    max_sources: int,
    official_only: bool = False,
) -> tuple:
    return (
        (product.ean13 or "").lower(),
        (product.upc or "").lower(),
        (product.article or "").lower(),
        (product.brand or "").lower(),
        product.name.lower().strip(),
        attribute.lower().strip(),
        max_sources,
        official_only,
    )


def get(key: tuple) -> AttributeResponse | None:
    with _lock:
        return _cache.get(key)


def set(key: tuple, value: AttributeResponse) -> None:
    with _lock:
        _cache[key] = value
