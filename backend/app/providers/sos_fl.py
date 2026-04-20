import re
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from rapidfuzz import fuzz

from app.events import emit_log
from app.models.entity import CleanedEntity
from app.providers.base import SOSResult, ScraperBlocked
from app.providers.browser import BrowserManager
from app.state import AppState

SEARCH_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"
NAME_MATCH_THRESHOLD = 0.85


class FLSunbizProvider:
    state_code = "FL"
    display_name = "Florida Sunbiz"

    def __init__(self, state: AppState, browser: BrowserManager):
        self.state = state
        self.browser = browser

    async def search(self, entity: CleanedEntity) -> Optional[SOSResult]:
        async with self.browser.page_lock:
            page = await self.browser.get_page("fl_sunbiz")

            for variant in entity.search_name_variants[:5]:
                await emit_log(self.state, "INFO", f"[FL] Searching: {variant!r}")
                try:
                    results = await self._search_variant(page, variant)
                except ScraperBlocked:
                    raise
                except (PlaywrightTimeoutError, Exception) as e:
                    await emit_log(
                        self.state, "WARN",
                        f"[FL] Variant {variant!r} failed: {type(e).__name__}",
                    )
                    continue

                best = self._pick_best_match(variant, results)
                if best:
                    detail = await self._fetch_detail(page, best["detail_url"])
                    return self._make_result(detail, best)

            return None

    async def _search_variant(self, page, variant: str) -> list[dict]:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")

        content = await page.content()
        if "captcha" in content.lower() or "cloudflare" in content.lower():
            raise ScraperBlocked("FL Sunbiz returned a challenge page")

        await page.fill('input[name="SearchTerm"]', variant)
        await page.click('input[type="submit"][value="Search Now"]')
        await page.wait_for_load_state("domcontentloaded")

        html = await page.content()
        return parse_fl_results_html(html, page.url)

    async def _fetch_detail(self, page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded")
        return await page.content()

    def _pick_best_match(self, search_name: str, results: list[dict]) -> Optional[dict]:
        if not results:
            return None
        scored = []
        for r in results:
            score = fuzz.token_sort_ratio(search_name.upper(), r["name"].upper()) / 100.0
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score, top = scored[0]
        if top_score >= NAME_MATCH_THRESHOLD:
            return top
        return None

    def _make_result(self, detail_html: str, list_item: dict) -> SOSResult:
        parsed = parse_fl_detail_html(detail_html)
        return SOSResult(
            filing_number=parsed.get("filing_number") or list_item.get("number", ""),
            entity_name=parsed.get("entity_name") or list_item.get("name", ""),
            status=parsed.get("status") or list_item.get("status", "Unknown"),
            principal_address=parsed.get("principal_address"),
            mailing_address=parsed.get("mailing_address"),
            registered_agent=parsed.get("registered_agent"),
            officers=parsed.get("officers", []),
            filing_date=parsed.get("filing_date"),
            source_url=list_item.get("detail_url"),
        )


# --- Pure-function HTML parsers (unit-testable without a browser) ---

def parse_fl_results_html(html: str, base_url: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    table = soup.find("table")
    if not table:
        return []

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[0].find("a")
        if not link:
            continue
        name = link.get_text(strip=True)
        detail_href = link.get("href", "")
        if detail_href.startswith("/"):
            detail_url = f"https://search.sunbiz.org{detail_href}"
        else:
            detail_url = detail_href
        number = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        status = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        if name and detail_url:
            results.append({
                "name": name,
                "number": number,
                "status": status,
                "detail_url": detail_url,
            })
    return results


def parse_fl_detail_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {
        "filing_number": None,
        "entity_name": None,
        "status": None,
        "principal_address": None,
        "mailing_address": None,
        "registered_agent": None,
        "officers": [],
        "filing_date": None,
    }

    title_span = soup.find("span", class_=re.compile(r"entityName|title", re.I))
    if title_span:
        out["entity_name"] = title_span.get_text(strip=True)

    for section in soup.find_all("div", class_=re.compile(r"detailSection")):
        label_elem = section.find(["label", "span", "h3"])
        if not label_elem:
            continue
        label_text = label_elem.get_text(strip=True)
        label = label_text.lower()

        if "document number" in label:
            value = section.get_text(strip=True).replace(label_text, "").strip()
            out["filing_number"] = value
        elif label == "status" and out["status"] is None:
            value = section.get_text(strip=True).replace(label_text, "").strip()
            out["status"] = value
        elif "filing date" in label or "date filed" in label:
            value = section.get_text(strip=True).replace(label_text, "").strip()
            out["filing_date"] = value

    out["principal_address"] = _extract_address_block(soup, "principal")
    out["mailing_address"] = _extract_address_block(soup, "mailing")
    out["registered_agent"] = _extract_registered_agent(soup)
    out["officers"] = _extract_officers_fl(soup)

    return out


def _extract_address_block(soup, kind: str) -> Optional[dict]:
    header = soup.find(
        lambda tag: tag.name in ("h3", "span", "label")
        and kind.lower() in tag.get_text(strip=True).lower()
    )
    if not header:
        return None
    container = header.find_parent("div") or header.find_next_sibling("div")
    if not container:
        return None
    lines = [
        line.strip()
        for line in container.get_text("\n", strip=True).split("\n")
        if line.strip() and kind.lower() not in line.lower()
    ]
    if not lines:
        return None
    return parse_address_lines(lines)


def _extract_registered_agent(soup) -> Optional[dict]:
    header = soup.find(
        lambda tag: tag.name in ("h3", "span", "label")
        and "registered agent" in tag.get_text(strip=True).lower()
    )
    if not header:
        return None
    container = header.find_parent("div") or header.find_next_sibling("div")
    if not container:
        return None
    lines = [
        line.strip()
        for line in container.get_text("\n", strip=True).split("\n")
        if line.strip() and "registered agent" not in line.lower()
    ]
    if not lines:
        return None
    return {
        "name": lines[0],
        "address": parse_address_lines(lines[1:]) if len(lines) > 1 else None,
    }


def _extract_officers_fl(soup) -> list[dict]:
    header = soup.find(
        lambda tag: tag.name in ("h3", "span", "label") and any(
            phrase in tag.get_text(strip=True).lower()
            for phrase in ("authorized person", "officer/director", "officer")
        )
    )
    if not header:
        return []
    section = header.find_parent("div") or header.find_next_sibling("div")
    if not section:
        return []
    officers: list[dict] = []
    title_pattern = re.compile(
        r"(MGR|MGRM|AMBR|PRES|VP|DIRECTOR|TRUSTEE|MEMBER)", re.I
    )
    for block in section.find_all(
        lambda t: t.name == "div"
        and t.find_all(string=title_pattern)
    ):
        text = block.get_text("\n", strip=True)
        lines = [line for line in text.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        title = lines[0].strip()
        name = lines[1].strip()
        address = parse_address_lines(lines[2:]) if len(lines) > 2 else None
        officers.append({"title": title, "name": name, "address": address})
    return officers


def parse_address_lines(lines: list[str]) -> Optional[dict]:
    if not lines:
        return None
    last = lines[-1]
    m = re.match(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", last)
    if m:
        return {
            "street": " ".join(lines[:-1]),
            "city": m.group(1).strip(),
            "state": m.group(2),
            "zip": m.group(3),
        }
    return {"street": " ".join(lines), "city": None, "state": None, "zip": None}
