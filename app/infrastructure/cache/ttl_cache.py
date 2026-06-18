from __future__ import annotations

import threading

from cachetools import TTLCache

from app.domain.product import ProductQuery


def make_key(
    product: ProductQuery,
    attribute: str,
    max_sources: int,
    official_only: bool = False,
) -> tuple:
    return (
        (product.ean13 or "").lower(),
        (product.upc or "").lower(),
        (product.mpn or "").lower(),
        (product.article or "").lower(),
        (product.brand or "").lower(),
        product.name.lower().strip(),
        attribute.lower().strip(),
        max_sources,
        official_only,
    )


class TTLCacheStore:
    def __init__(self, max_size: int, ttl_seconds: int):
        self._cache: TTLCache = TTLCache(maxsize=max_size, ttl=ttl_seconds)
        self._lock = threading.Lock()

    def get(self, key: tuple):
        with self._lock:
            return self._cache.get(key)

    def set(self, key: tuple, value) -> None:
        with self._lock:
            self._cache[key] = value
