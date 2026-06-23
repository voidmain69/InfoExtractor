"""Edge protections: API-key auth and a simple per-client rate limit.

The service drives outbound searches, page fetches and headless-browser
renders, so an unauthenticated, unlimited endpoint is both an open SSRF proxy
and a cheap DoS amplifier. These middlewares close that gap; both no-op only
when explicitly left unconfigured (auth) and are always on for rate limiting.
"""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Endpoints that must stay reachable without a key (liveness + API docs).
_AUTH_EXEMPT = {"/health", "/docs", "/redoc", "/openapi.json"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Require a matching ``X-API-Key`` header when an API key is configured."""

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self._api_key = api_key or ""
        if not self._api_key:
            logger.warning(
                "API_KEY is not set — all endpoints are unauthenticated. "
                "Set API_KEY (and keep the service off public networks)."
            )

    async def dispatch(self, request: Request, call_next):
        if not self._api_key or request.url.path in _AUTH_EXEMPT:
            return await call_next(request)
        provided = request.headers.get("x-api-key", "")
        if not _constant_time_eq(provided, self._api_key):
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-client-IP rate limit. Disabled when max_requests <= 0."""

    def __init__(self, app, max_requests: int, window_seconds: float):
        super().__init__(app)
        self._max = max_requests
        self._window = max(1.0, window_seconds)
        self._hits: dict[str, tuple[float, int]] = {}

    async def dispatch(self, request: Request, call_next):
        if self._max <= 0 or request.url.path == "/health":
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start, count = self._hits.get(client, (now, 0))

        if now - window_start >= self._window:
            window_start, count = now, 0  # window expired → reset

        count += 1
        self._hits[client] = (window_start, count)
        self._maybe_prune(now)

        if count > self._max:
            retry = max(0, int(self._window - (now - window_start)))
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)

    def _maybe_prune(self, now: float) -> None:
        """Drop stale buckets so the map can't grow unbounded over time."""
        if len(self._hits) < 4096:
            return
        stale = [ip for ip, (start, _) in self._hits.items()
                 if now - start >= self._window]
        for ip in stale:
            self._hits.pop(ip, None)


def _constant_time_eq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
