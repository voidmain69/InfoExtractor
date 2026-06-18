"""Playwright-based page fetcher for JS-rendered content with spec trigger clicks."""
from __future__ import annotations

import logging
import random
import re

from app.core.config import settings
from app.infrastructure.fetch.http_fetcher import _USER_AGENTS, _pick_proxy

logger = logging.getLogger(__name__)

_SPEC_TEXTS = [
    "specifications", "specs", "technical specifications",
    "technical details", "tech specs", "details", "full specifications",
    "характеристики", "технічні характеристики",
]

_SHOW_MORE_TEXTS = [
    "show more", "show all", "read more", "expand", "view all", "load more",
]

_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
]

# Injected before every page load to mask headless/webdriver fingerprints.
_STEALTH_SCRIPT = """
() => {
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [1, 2, 3, 4, 5];
            arr.item = i => arr[i];
            arr.namedItem = () => null;
            arr.refresh = () => {};
            return arr;
        }
    });
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
    window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}, app: {}};
    const orig = window.Notification;
    if (orig) {
        Object.defineProperty(window, 'Notification', {
            get: () => { const n = orig; n.permission = 'default'; return n; }
        });
    }
}
"""

# Realistic viewport sizes paired with the matching UA index.
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]


async def _click_spec_triggers(page) -> None:
    """Click tabs/buttons that may reveal hidden specification content."""
    nav_timeout = 4000

    # Spec-related tabs (highest priority — reveals a whole section)
    for text in _SPEC_TEXTS:
        try:
            loc = page.get_by_role("tab", name=re.compile(text, re.I))
            if await loc.count() > 0:
                await loc.first.click()
                await page.wait_for_load_state("networkidle", timeout=nav_timeout)
                return
        except Exception:
            pass

    # Spec buttons
    for text in _SPEC_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(text, re.I))
            if await loc.count() > 0:
                await loc.first.click()
                await page.wait_for_load_state("networkidle", timeout=nav_timeout)
                return
        except Exception:
            pass

    # "Show more" type buttons — may expand collapsed spec rows
    for text in _SHOW_MORE_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(text, re.I))
            if await loc.count() > 0:
                await loc.first.click()
                await page.wait_for_timeout(1500)
        except Exception:
            pass


async def fetch_with_js(url: str) -> str | None:
    """Fetch URL via headless Chromium with stealth patches and spec trigger clicks."""
    if not settings.use_playwright:
        return None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright not installed; JS fetching disabled")
        return None

    timeout_ms = int(settings.playwright_timeout_seconds * 1000)
    ua = random.choice(_USER_AGENTS)
    viewport = random.choice(_VIEWPORTS)
    proxy = _pick_proxy()
    proxy_cfg = {"server": proxy} if proxy else None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
            ctx = await browser.new_context(
                user_agent=ua,
                viewport=viewport,
                locale="en-US",
                timezone_id="America/New_York",
                proxy=proxy_cfg,
            )
            # Patch fingerprint in every frame before any script runs.
            await ctx.add_init_script(_STEALTH_SCRIPT)
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await _click_spec_triggers(page)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        logger.debug("playwright fetch failed for %s: %s", url, exc)
        return None
