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
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}


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


async def _host_is_public(host: str) -> bool:
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except Exception as exc:
        logger.debug("DNS resolution failed for %s: %s", host, exc)
        return False
    if not infos:
        return False
    # Every resolved address must be public — a single internal answer is enough
    # to abuse, so reject the host if any record is blocked.
    for info in infos:
        ip = info[4][0]
        if _ip_blocked(ip):
            return False
    return True


async def is_safe_url(url: str) -> bool:
    """Return True only for http(s) URLs whose host resolves to public IPs."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    host = parsed.hostname
    if not host:
        return False
    return await _host_is_public(host)
