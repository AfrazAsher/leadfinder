import asyncio
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from app.core.config import settings


class BrowserManager:
    """
    Lazy Playwright Chromium wrapper — the browser is launched on the first
    `get_page()` call, not on `__aenter__`, so entities that never hit a
    registered SOS scraper don't pay the launch cost.
    """

    def __init__(self, headless: Optional[bool] = None):
        self.headless = headless if headless is not None else settings.headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._pages: dict[str, object] = {}
        self._launched = False
        self._launch_lock = asyncio.Lock()
        self.page_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for page in self._pages.values():
            try:
                await page.close()
            except Exception:
                pass
        self._pages.clear()
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

    async def _ensure_launched(self) -> None:
        if self._launched:
            return
        async with self._launch_lock:
            if self._launched:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self._launched = True

    async def get_page(self, key: str):
        await self._ensure_launched()
        if key not in self._pages:
            assert self._context is not None
            page = await self._context.new_page()
            page.set_default_timeout(15000)
            self._pages[key] = page
        return self._pages[key]
