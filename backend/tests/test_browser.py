import pytest

from app.providers.browser import BrowserManager


@pytest.mark.asyncio
@pytest.mark.slow
async def test_browser_opens_and_closes():
    async with BrowserManager(headless=True) as bm:
        page = await bm.get_page("test")
        await page.goto("about:blank")
        assert page.url == "about:blank"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_browser_reuses_named_pages():
    async with BrowserManager(headless=True) as bm:
        p1 = await bm.get_page("shared")
        p2 = await bm.get_page("shared")
        assert p1 is p2
