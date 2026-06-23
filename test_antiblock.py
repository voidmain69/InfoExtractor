"""Anti-blocking layer smoke tests — run inside the container."""
import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")

PASS = "PASS"
FAIL = "FAIL"


def section(title):
    print(f"\n=== {title} ===")


# ── 1. UA & Accept-Language rotation ─────────────────────────────────────────
section("UA + Accept-Language rotation")
from app.infrastructure.fetch.http_fetcher import _USER_AGENTS, _random_headers

seen_ua, seen_lang = set(), set()
for _ in range(30):
    h = _random_headers()
    seen_ua.add(h["User-Agent"])
    seen_lang.add(h["Accept-Language"])

print(f"unique UAs:   {len(seen_ua)}/{len(_USER_AGENTS)}  ({PASS if len(seen_ua) == len(_USER_AGENTS) else FAIL})")
for ua in sorted(seen_ua):
    print(f"  {ua[:85]}")
print(f"unique langs: {len(seen_lang)}  ({PASS if len(seen_lang) >= 3 else FAIL})")
for l in seen_lang:
    print(f"  {l}")


# ── 2. Proxy parsing ──────────────────────────────────────────────────────────
section("Proxy parsing")
import os
os.environ["PROXY_LIST"] = "http://p1:3128,http://p2:3128,http://p3:3128"
from app.core.config import Settings
s = Settings()
proxies = [p.strip() for p in s.proxy_list.split(",") if p.strip()]
print(f"parsed {len(proxies)} proxies: {proxies}  ({PASS if len(proxies) == 3 else FAIL})")


# ── 3. Retry: 429 → 503 → 200 ────────────────────────────────────────────────
section("Retry logic (429 → 503 → 200)")

async def _test_retry():
    from app.infrastructure.fetch import http_fetcher as hf

    call_count = 0
    responses = [
        hf._RawResponse(429, {}, ""),
        hf._RawResponse(503, {}, ""),
        hf._RawResponse(200, {}, "<html>ok</html>"),
    ]

    # _get_with_retry now follows/validates redirects via _request_once; patch
    # that seam so the retry test stays focused on status-driven retries.
    async def fake_request_once(client, url):
        nonlocal call_count
        r = responses[min(call_count, len(responses) - 1)]
        call_count += 1
        return r

    async def no_sleep(t):
        pass

    with patch.object(hf, "_request_once", fake_request_once), patch("asyncio.sleep", no_sleep):
        result = await hf._get_with_retry(MagicMock(), "http://example.com")

    ok = result is not None and result.status_code == 200 and call_count == 3
    print(f"attempts: {call_count}, final status: {result.status_code}  ({PASS if ok else FAIL})")

asyncio.run(_test_retry())


# ── 4. Retry exhaustion → None ────────────────────────────────────────────────
section("Retry exhaustion (all 429) → returns None")

async def _test_exhaust():
    from app.infrastructure.fetch import http_fetcher as hf

    async def always_429(client, url):
        return hf._RawResponse(429, {}, "")

    async def no_sleep(t):
        pass

    with patch.object(hf, "_request_once", always_429), patch("asyncio.sleep", no_sleep):
        result = await hf._get_with_retry(MagicMock(), "http://example.com")

    print(f"result is None: {result is None}  ({PASS if result is None else FAIL})")

asyncio.run(_test_exhaust())


# ── 5. Jitter timing ─────────────────────────────────────────────────────────
section("Jitter timing (5 concurrent, jitter_max=0.2s)")

async def _test_jitter():
    from app.core import config as cfg
    cfg.settings.fetch_jitter_max = 0.2
    cfg.settings.fetch_retry_attempts = 1

    from app.infrastructure.fetch import http_fetcher as hf

    async def fake_request_once(client, url):
        return hf._RawResponse(200, {}, "ok")

    sem = asyncio.Semaphore(5)
    with patch.object(hf, "_request_once", fake_request_once):
        start = time.monotonic()
        await asyncio.gather(*[hf._fetch_single(f"http://ex.com/{i}", "", sem, MagicMock()) for i in range(5)])
        elapsed = time.monotonic() - start
    print(f"elapsed: {elapsed:.3f}s  ({PASS if elapsed > 0.02 else 'WARN: no jitter detected'})")

asyncio.run(_test_jitter())


# ── 6. Stealth script content ─────────────────────────────────────────────────
section("Playwright stealth script")
from app.infrastructure.fetch.browser_fetcher import _STEALTH_SCRIPT, _VIEWPORTS

patches = [
    ("'webdriver'", "navigator.webdriver patched"),
    ("'plugins'",   "navigator.plugins patched"),
    ("'languages'", "navigator.languages patched"),
    ("window.chrome", "window.chrome injected"),
    ("hardwareConcurrency", "hardwareConcurrency patched"),
]
for needle, label in patches:
    ok = needle in _STEALTH_SCRIPT
    print(f"  {PASS if ok else FAIL}: {label}")

print(f"viewports: {[str(v['width'])+'x'+str(v['height']) for v in _VIEWPORTS]}")


# ── 7. Client Hints / Sec-Fetch consistency ──────────────────────────────────
section("Client Hints + Sec-Fetch headers")
from app.infrastructure.fetch.http_fetcher import _UA_PROFILES

saw_chromium_hint = False
all_consistent = True
for _ in range(40):
    h = _random_headers()
    for must in ("Sec-Fetch-Dest", "Sec-Fetch-Mode", "Referer"):
        if must not in h:
            all_consistent = False
    is_chromium = "Chrome" in h["User-Agent"] or "Edg/" in h["User-Agent"]
    has_hint = "Sec-CH-UA" in h
    # Chromium UAs must carry Client Hints; Firefox/Safari must not.
    if is_chromium != has_hint:
        all_consistent = False
    if has_hint:
        saw_chromium_hint = True
print(f"sec-fetch headers always present: {all_consistent}  ({PASS if all_consistent else FAIL})")
print(f"client hints paired with UA family: {saw_chromium_hint}  ({PASS if saw_chromium_hint and all_consistent else FAIL})")


# ── 8. Spec-reveal trigger vocabulary (uk / ru / en) ──────────────────────────
section("Spec-reveal trigger vocabulary")
from app.infrastructure.fetch.browser_fetcher import _SPEC_RX, _SHOW_MORE_RX, _CONSENT_RX

cases = [
    (_SHOW_MORE_RX, "Докладніше", True),
    (_SHOW_MORE_RX, "Показати ще", True),
    (_SHOW_MORE_RX, "Развернуть", True),
    (_SHOW_MORE_RX, "Show more", True),
    (_SPEC_RX, "Технічні характеристики", True),
    (_SPEC_RX, "Specifications", True),
    (_CONSENT_RX, "Прийняти всі", True),
    (_CONSENT_RX, "Accept all cookies", True),
    (_SHOW_MORE_RX, "Add to cart", False),
]
for rx, text, expected in cases:
    got = rx.search(text) is not None
    print(f"  {PASS if got == expected else FAIL}: match {text!r} == {expected}")


# ── 9. SSRF guard: block internal addresses & non-http schemes ───────────────
section("SSRF guard (private/loopback/link-local + scheme)")
from app.infrastructure.fetch.url_guard import _ip_blocked, is_safe_url

blocked_ips = [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "::1", "::ffff:127.0.0.1", "fc00::1",
]
public_ips = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]

ip_ok = True
for ip in blocked_ips:
    if not _ip_blocked(ip):
        ip_ok = False
        print(f"  {FAIL}: {ip} should be blocked")
for ip in public_ips:
    if _ip_blocked(ip):
        ip_ok = False
        print(f"  {FAIL}: {ip} should be allowed")
print(f"IP classification correct: {ip_ok}  ({PASS if ip_ok else FAIL})")

# Literal-IP and scheme checks need no DNS.
scheme_cases = [
    ("ftp://example.com/x", False),
    ("file:///etc/passwd", False),
    ("http://169.254.169.254/latest/meta-data/", False),  # cloud metadata
    ("http://127.0.0.1:11434/", False),                    # local Ollama
    ("http://[::1]/", False),
]
scheme_ok = True
for url, expected in scheme_cases:
    got = asyncio.run(is_safe_url(url))
    if got != expected:
        scheme_ok = False
        print(f"  {FAIL}: is_safe_url({url!r}) == {got}, expected {expected}")
print(f"scheme/internal URL rejection correct: {scheme_ok}  ({PASS if scheme_ok else FAIL})")


print("\n--- all tests done ---")
