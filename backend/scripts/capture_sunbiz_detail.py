"""Capture real FL Sunbiz results + detail HTML for parser analysis.

Saves:
    backend/tests/fixtures/sos/fl_real_results.html
    backend/tests/fixtures/sos/fl_real_detail.html

Prints the detail HTML to stdout (truncated to 8000 chars).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.async_api import async_playwright  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "sos"
SEARCH_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"
SEARCH_TERM = "DISNEY DESTINATIONS"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        page.set_default_timeout(30000)

        print(f"Navigating to: {SEARCH_URL}")
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")

        print(f"Searching: {SEARCH_TERM!r}")
        await page.fill('input[name="SearchTerm"]', SEARCH_TERM)
        await page.click('input[type="submit"][value="Search Now"]')
        await page.wait_for_load_state("domcontentloaded")

        results_html = await page.content()
        results_path = FIXTURES / "fl_real_results.html"
        results_path.write_text(results_html, encoding="utf-8")
        print(f"Saved results page: {results_path} ({len(results_html)} bytes)")
        print(f"Results URL: {page.url}")

        # Click first <a> inside the results <table>
        first_link = page.locator("table a").first
        link_text = (await first_link.text_content()) or "<unknown>"
        print(f"Clicking first result link: {link_text.strip()!r}")
        await first_link.click()
        await page.wait_for_load_state("domcontentloaded")

        detail_html = await page.content()
        detail_path = FIXTURES / "fl_real_detail.html"
        detail_path.write_text(detail_html, encoding="utf-8")
        print(f"Saved detail page: {detail_path} ({len(detail_html)} bytes)")
        print(f"Detail URL: {page.url}")

        print("\n===== DETAIL HTML =====\n")
        if len(detail_html) < 8000:
            print(detail_html)
        else:
            print(detail_html[:8000])
            print(f"\n... [truncated, full file saved to {FIXTURES / 'fl_real_detail.html'}]")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
