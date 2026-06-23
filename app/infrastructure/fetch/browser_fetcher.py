"""Playwright-based page fetcher for JS-rendered content.

Many shops hide the full spec table behind interaction: a "Specifications" tab,
a collapsed accordion, or a "Show more" / "Докладніше" button. Static HTML then
only carries a teaser. This fetcher renders the page in headless Chromium with
stealth patches, dismisses consent overlays, scrolls to trigger lazy-loading,
and then reveals every spec section it can find before returning the HTML.
"""
from __future__ import annotations

import logging
import random
import re

from app.core.config import settings
from app.infrastructure.fetch.http_fetcher import _USER_AGENTS, _pick_proxy

logger = logging.getLogger(__name__)

# Tabs/sections that hold specifications (uk / ru / en).
_SPEC_TEXTS = [
    "specifications", "specification", "specs", "technical specifications",
    "technical details", "tech specs", "full specifications", "details",
    "характеристики", "технічні характеристики", "характеристики товару",
    "технічні дані", "усі характеристики", "всі характеристики",
    "характеристики товара", "технические характеристики", "описание",
]

# "Show more" style triggers that expand collapsed spec rows (uk / ru / en).
_SHOW_MORE_TEXTS = [
    "show more", "show all", "read more", "view more", "view all", "see more",
    "load more", "more details", "expand", "full specs",
    "докладніше", "детальніше", "показати ще", "показати все",
    "показати всі характеристики", "усі характеристики", "всі характеристики",
    "розгорнути", "більше", "ще",
    "подробнее", "показать ещё", "показать еще", "показать все", "развернуть",
]

# Consent / cookie banners overlay the page and swallow clicks; dismiss first.
_CONSENT_TEXTS = [
    "accept all", "accept cookies", "i accept", "accept", "agree", "got it",
    "allow all", "ok", "i agree", "continue",
    "прийняти", "прийняти всі", "погоджуюсь", "дозволити", "зрозуміло",
    "принять", "принять все", "согласен", "хорошо",
]

_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-blink-features=AutomationControlled",
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

# Open every native <details> and any aria-collapsed region without relying on a
# click — covers spec sections built as <details>/<summary> or CSS accordions.
_REVEAL_SCRIPT = """
() => {
    let n = 0;
    document.querySelectorAll('details:not([open])').forEach(d => { d.open = true; n++; });
    document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
        try { el.setAttribute('aria-expanded', 'true'); } catch (e) {}
    });
    return n;
}
"""

# Realistic viewport sizes.
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]

_MAX_EXPAND_ROUNDS = 3   # repeat passes — expanding one block can reveal more
_MAX_CLICKS_PER_PASS = 12


def _rx(texts: list[str]) -> re.Pattern:
    """Case-insensitive regex matching any of the phrases (longest first)."""
    parts = sorted((re.escape(t) for t in texts), key=len, reverse=True)
    return re.compile("|".join(parts), re.I)


_SPEC_RX = _rx(_SPEC_TEXTS)
_SHOW_MORE_RX = _rx(_SHOW_MORE_TEXTS)
_CONSENT_RX = _rx(_CONSENT_TEXTS)


async def _dismiss_consent(page) -> None:
    """Close a cookie/consent banner so it stops intercepting clicks."""
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=_CONSENT_RX)
            if await loc.count() > 0:
                await loc.first.click(timeout=2000)
                await page.wait_for_timeout(300)
                return
        except Exception:
            pass
    # Some banners label the accept control only via id/class, not a role.
    for sel in ("#onetrust-accept-btn-handler", "button[id*='accept']",
                "button[class*='accept']", "[aria-label*='accept' i]"):
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click(timeout=2000)
                await page.wait_for_timeout(300)
                return
        except Exception:
            pass


async def _scroll_page(page) -> None:
    """Scroll down in steps to trigger lazy-loaded spec sections."""
    try:
        for _ in range(6):
            await page.mouse.wheel(0, 2200)
            await page.wait_for_timeout(350)
        await page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass


async def _click_matching(page, rx: re.Pattern, roles: tuple[str, ...]) -> int:
    """Click up to a cap of currently-visible elements whose label matches rx.

    Returns the number of successful clicks. Elements are re-resolved each pass
    because a click can re-render the DOM. Callers choose which roles are safe:
    "link" can navigate away, so it's only used where that's acceptable.
    """
    clicks = 0
    for role in roles:
        try:
            loc = page.get_by_role(role, name=rx)
            count = min(await loc.count(), _MAX_CLICKS_PER_PASS)
        except Exception:
            continue
        for i in range(count):
            try:
                el = loc.nth(i)
                if not await el.is_visible():
                    continue
                await el.click(timeout=1500)
                clicks += 1
                await page.wait_for_timeout(400)
            except Exception:
                continue
            if clicks >= _MAX_CLICKS_PER_PASS:
                return clicks
    return clicks


async def _reveal_specs(page) -> None:
    """Reveal hidden spec content: open spec tabs, expand accordions & "show more".

    Runs several passes because expanding one section frequently unveils further
    collapsed rows or a nested "show all" control.
    """
    # Spec tabs/sections first — surfaces the whole block (don't stop here, a
    # revealed block often still has its own "show more" inside). Links are
    # allowed here: a "Specifications" link usually leads to the full spec page.
    await _click_matching(page, _SPEC_RX, roles=("tab", "button", "link"))

    for _ in range(_MAX_EXPAND_ROUNDS):
        try:
            await page.evaluate(_REVEAL_SCRIPT)
        except Exception:
            pass
        # "Show more" only via buttons/tabs — a link could navigate off-page and
        # discard the specs we already revealed.
        clicked = await _click_matching(page, _SHOW_MORE_RX, roles=("button", "tab"))
        if clicked == 0:
            break

    # Let any final lazy content settle.
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass


async def fetch_with_js(url: str) -> str | None:
    """Fetch URL via headless Chromium with stealth patches and spec reveal."""
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
            await _dismiss_consent(page)
            await _scroll_page(page)
            await _reveal_specs(page)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        logger.debug("playwright fetch failed for %s: %s", url, exc)
        return None
