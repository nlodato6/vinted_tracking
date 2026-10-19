"""Playwright browser context factory with stealth configurations."""

import asyncio
import random
from typing import Optional
from playwright.async_api import async_playwright, BrowserContext, Page
from src.config import settings

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});
"""


async def get_browser_context(headless: Optional[bool] = None, use_storage_state: bool = True) -> BrowserContext:
    """Creates a configured browser context with stealth injections and persistent session state."""
    from playwright_stealth import Stealth

    is_headless = headless if headless is not None else settings.headless
    pw = await async_playwright().start()

    launch_kwargs = {
        "headless": is_headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        "ignore_default_args": ["--enable-automation"],
    }

    # Attempt to launch system Google Chrome first (bypasses bot checks and DataDome far more reliably)
    try:
        browser = await pw.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception:
        browser = await pw.chromium.launch(**launch_kwargs)

    state_path = settings.full_storage_state_path if (use_storage_state and settings.full_storage_state_path.exists()) else None

    context = await browser.new_context(
        storage_state=str(state_path) if state_path else None,
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 800},
        device_scale_factor=1,
        locale="en-US",
        timezone_id="America/Chicago",
    )

    # Apply comprehensive playwright-stealth evasions (webdriver, chrome runtime, plugins, media codecs, etc.)
    stealth = Stealth()
    await stealth.apply_stealth_async(context)
    return context


async def human_delay(min_sec: float = 1.0, max_sec: float = 2.5):
    """Introduces human-like jitter between automated interactions."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))
