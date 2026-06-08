# src/scrapers/playwright_fetcher.py

"""
Playwright-based fetcher for JS-heavy and anti-bot protected sources.

Uses a single shared Chromium browser (lazy-launched) with per-request
isolated contexts. Concurrent fetches are capped by PLAYWRIGHT_CONCURRENCY
(default 3) to keep memory usage acceptable on constrained hosts (Render free tier).

Deploy-time setup (run once):
    pip install playwright
    playwright install chromium --with-deps
"""

import asyncio
import os
from typing import Optional
from loguru import logger

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning(
        "playwright not installed — Playwright fetcher disabled. "
        "Run: pip install playwright && playwright install chromium --with-deps"
    )

_browser: Optional["Browser"] = None
_playwright_instance = None
_browser_lock: Optional[asyncio.Lock] = None
_sem: Optional[asyncio.Semaphore] = None

_PLAYWRIGHT_CONCURRENCY = int(os.getenv("PLAYWRIGHT_CONCURRENCY", "3"))
_PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))
_PLAYWRIGHT_JS_WAIT_MS = int(os.getenv("PLAYWRIGHT_JS_WAIT_MS", "1500"))

# Resource types to block — speeds up page loads significantly
_BLOCKED_RESOURCES = {"image", "media", "font", "websocket"}

# Domains known to need JS rendering (supplement to ANTI_BOT_DOMAINS in scrapling_fetcher)
JS_RENDER_DOMAINS = {
    "thewire.in",
    "pakistantoday.com.pk",
    "tribune.com.pk",
    "stuff.co.nz",
    "nzherald.co.nz",
    "thestar.com",
    "theedgemalaysia.com",
    "saudigazette.com.sa",
    "thepeninsulaqatar.com",
    "tehrantimes.com",
    "independent.co.uk",
    "skynews.com",
    "sky.com",
}


def needs_playwright(url: str) -> bool:
    return any(domain in url for domain in JS_RENDER_DOMAINS)


def _get_lock() -> asyncio.Lock:
    global _browser_lock
    if _browser_lock is None:
        _browser_lock = asyncio.Lock()
    return _browser_lock


def _get_semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_PLAYWRIGHT_CONCURRENCY)
    return _sem


async def _get_browser() -> "Browser":
    """Return the shared Chromium browser, launching it on first call."""
    global _browser, _playwright_instance
    lock = _get_lock()
    async with lock:
        if _browser is None or not _browser.is_connected():
            logger.info("Playwright: launching Chromium browser")
            _playwright_instance = await async_playwright().start()
            _browser = await _playwright_instance.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-extensions",
                    "--disable-gpu",
                    "--window-size=1920,1080",
                    "--disable-setuid-sandbox",
                    "--single-process",
                ],
            )
            logger.info("Playwright: Chromium browser ready")
    return _browser


async def _new_stealth_context(browser: "Browser") -> "BrowserContext":
    """Create an isolated browser context with anti-detection patches."""
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        java_script_enabled=True,
        accept_downloads=False,
        extra_http_headers={
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = {runtime: {}};
        Object.defineProperty(navigator, 'permissions', {
            get: () => ({query: () => Promise.resolve({state: 'granted'})})
        });
    """)
    return context


async def fetch_with_playwright(url: str) -> Optional[str]:
    """
    Fetch a URL using a stealthy Playwright Chromium context.
    Waits for DOM content + a brief JS settle delay, then returns HTML.
    Returns None on any failure.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None

    sem = _get_semaphore()
    context: Optional["BrowserContext"] = None
    page: Optional["Page"] = None

    async with sem:
        try:
            browser = await _get_browser()
            context = await _new_stealth_context(browser)
            page = await context.new_page()

            # Block heavy resource types to speed up load
            async def _block(route, request):
                if request.resource_type in _BLOCKED_RESOURCES:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", _block)

            logger.debug(f"Playwright: fetching {url[:70]}")
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=_PLAYWRIGHT_TIMEOUT_MS,
            )

            if response is None:
                logger.debug(f"Playwright: no response for {url[:70]}")
                return None

            if response.status >= 400:
                logger.debug(f"Playwright: HTTP {response.status} for {url[:70]}")
                return None

            # Let JS finish rendering
            await asyncio.sleep(_PLAYWRIGHT_JS_WAIT_MS / 1000)

            html = await page.content()
            if len(html) > 500:
                logger.info(f"✅ Playwright fetch success ({response.status}): {url[:70]}")
                return html

            logger.debug(f"Playwright: response too short ({len(html)}B) for {url[:70]}")

        except Exception as e:
            logger.debug(f"Playwright fetch error for {url[:70]}: {e}")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    return None


async def close_playwright():
    """Gracefully close the shared browser. Call once at pipeline shutdown."""
    global _browser, _playwright_instance
    if _browser:
        try:
            await _browser.close()
            logger.debug("Playwright: browser closed")
        except Exception:
            pass
        _browser = None
    if _playwright_instance:
        try:
            await _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None
