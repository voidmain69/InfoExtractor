"""SSRF guard for outbound page fetches.

The service follows URLs that originate from search-engine results and from
HTTP redirects — both attacker-influenceable. Without a guard, a crafted page
that redirects to ``http://169.254.169.254/`` or ``http://host.docker.internal``
would make us fetch (and, under Playwright, execute JS against) internal
services and cloud metadata endpoints.

We resolve every host and reject any answer that maps to a non-global address
(private, loopback, link-local, multicast, reserved, CGNAT, etc.). This also
transparently covers names like ``host.docker.internal`` and
``metadata.google.internal`` because they resolve into those ranges.

Residual risk: DNS rebinding between this check and the actual connection. For
the search-result/redirect threat model this validation is a proportionate
mitigation; a fully airtight fix would pin the validated IP for the connection.

Proxy-only egress: when all outbound traffic is forced through an HTTP proxy,
the proxy — not this process — performs name resolution, and local DNS for
external names is typically unavailable. In that mode ``getaddrinfo`` fails for
every public host, so resolving-then-checking would block everything. We instead
keep the two checks that need no DNS — non-global IP *literals* (e.g.
``169.254.169.254``) and internal-looking hostnames (no dot, or an internal
suffix like ``.internal``/``.local``/``.localhost``) are still rejected — and
otherwise defer to the proxy boundary for egress. When a proxy is *not*
configured, behaviour is unchanged: hosts are resolved and fail closed.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}

# Hostname suffixes / names that always denote a non-public target, checked
# without DNS so the proxy-only path still blocks the documented SSRF names.
_INTERNAL_SUFFIXES = (".internal", ".local", ".localhost", ".lan", ".intranet")
_INTERNAL_NAMES = {"localhost", "host.docker.internal", "metadata.google.internal"}


def _proxy_configured() -> bool:
    """True if outbound fetches are forced through an HTTP proxy."""
    if settings.proxy_list.strip():
        return True
    return any(
        os.environ.get(var)
        for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
    )


def _host_is_internal_name(host: str) -> bool:
    """Reject internal-looking hostnames using string rules only (no DNS)."""
    h = host.strip(".").lower()
    if not h:
        return True
    if h in _INTERNAL_NAMES:
        return True
    if any(h == s.lstrip(".") or h.endswith(s) for s in _INTERNAL_SUFFIXES):
        return True
    # A bare single-label hostname (no dot) can only be an internal/short name.
    if "." not in h:
        return True
    return False


def _ip_blocked(ip_str: str) -> bool:
    """True if the literal IP is anything but a normal public address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → fail closed
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) before checking.
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global is False for private, loopback, link-local, multicast,
    # reserved, unspecified and CGNAT (100.64/10) ranges.
    return not ip.is_global


async def is_safe_url(url: str) -> bool:
    """Return True only for http(s) URLs whose host resolves to public IPs.

    Under proxy-only egress (no local DNS), fall back to literal/name checks
    that need no resolution and defer the rest to the proxy boundary.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    host = parsed.hostname
    if not host:
        return False

    # IP literal: always check directly — no DNS needed, blocks metadata/internal IPs.
    try:
        ipaddress.ip_address(host)
        return not _ip_blocked(host)
    except ValueError:
        pass  # not a literal → it's a hostname

    # Internal-looking names are rejected regardless of egress mode.
    if _host_is_internal_name(host):
        return False

    # Try to resolve and apply the full public-IP check.
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except Exception as exc:
        # No local DNS. If a proxy mediates egress (and does its own resolution),
        # the local resolver's blindness isn't a safety signal — allow the host;
        # the literal/internal-name checks above still apply. Otherwise fail closed.
        if _proxy_configured():
            logger.debug("DNS unavailable for %s; allowing via proxy egress", host)
            return True
        logger.debug("DNS resolution failed for %s: %s", host, exc)
        return False
    if not infos:
        return False
    for info in infos:
        if _ip_blocked(info[4][0]):
            return False
    return True
