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
    from app.infrastructure.fetch.http_fetcher import _get_with_retry

    call_count = 0
    responses = [
        MagicMock(status_code=429, headers={}),
        MagicMock(status_code=503, headers={}),
        MagicMock(status_code=200, headers={}, text="<html>ok</html>"),
    ]

    async def fake_get(url, headers=None):
        nonlocal call_count
        r = responses[min(call_count, len(responses) - 1)]
        call_count += 1
        return r

    client = MagicMock()
    client.get = fake_get

    async def no_sleep(t):
        pass

    with patch("asyncio.sleep", no_sleep):
        result = await _get_with_retry(client, "http://example.com")

    ok = result is not None and result.status_code == 200 and call_count == 3
    print(f"attempts: {call_count}, final status: {result.status_code}  ({PASS if ok else FAIL})")

asyncio.run(_test_retry())


# ── 4. Retry exhaustion → None ────────────────────────────────────────────────
section("Retry exhaustion (all 429) → returns None")

async def _test_exhaust():
    from app.infrastructure.fetch.http_fetcher import _get_with_retry

    async def always_429(url, headers=None):
        return MagicMock(status_code=429, headers={})

    client = MagicMock()
    client.get = always_429

    async def no_sleep(t):
        pass

    with patch("asyncio.sleep", no_sleep):
        result = await _get_with_retry(client, "http://example.com")

    print(f"result is None: {result is None}  ({PASS if result is None else FAIL})")

asyncio.run(_test_exhaust())


# ── 5. Jitter timing ─────────────────────────────────────────────────────────
section("Jitter timing (5 concurrent, jitter_max=0.2s)")

async def _test_jitter():
    from app.core import config as cfg
    cfg.settings.fetch_jitter_max = 0.2
    cfg.settings.fetch_retry_attempts = 1

    async def fake_get(url, headers=None):
        return MagicMock(status_code=200, headers={}, text="ok")

    client = MagicMock()
    client.get = fake_get
    sem = asyncio.Semaphore(5)

    from app.infrastructure.fetch.http_fetcher import _fetch_single
    start = time.monotonic()
    await asyncio.gather(*[_fetch_single(f"http://ex.com/{i}", "", sem, client) for i in range(5)])
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


print("\n--- all tests done ---")
