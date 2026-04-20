import re
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from app.events import emit_log
from app.models.entity import CleanedEntity
from app.providers.base import SOSResult, ScraperBlocked
from app.providers.browser import BrowserManager
from app.state import AppState

SEARCH_BASE = "https://www.sosnc.gov/online_services/search/by_title/_Business_Registration"
NAME_MATCH_THRESHOLD = 0.85


class NCSOSProvider:
    state_code = "NC"
    display_name = "NC Secretary of State"

    def __init__(self, state: AppState, browser: BrowserManager):
        self.state = state
        self.browser = browser

    async def search(self, entity: CleanedEntity) -> Optional[SOSResult]:
        async with self.browser.page_lock:
            page = await self.browser.get_page("nc_sos")
            for variant in entity.search_name_variants[:5]:
                await emit_log(self.state, "INFO", f"[NC] Searching: {variant!r}")
                try:
                    results = await self._search_variant(page, variant)
                except ScraperBlocked:
                    raise
                except Exception as e:
                    await emit_log(
                        self.state, "WARN",
                        f"[NC] Variant {variant!r} failed: {type(e).__name__}",
                    )
                    continue
                best = self._pick_best_match(variant, results)
                if best:
                    detail = await self._fetch_detail(page, best["detail_url"])
                    return self._make_result(detail, best)
            return None

    async def _search_variant(self, page, variant: str) -> list[dict]:
        url = f"{SEARCH_BASE}?Words={quote_plus(variant)}&SearchType=0"
        await page.goto(url, wait_until="domcontentloaded")
        content = await page.content()
        if "captcha" in content.lower() or "cloudflare" in content.lower():
            raise ScraperBlocked("NC SOS returned a challenge page")
        return parse_nc_results_html(content)

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
        parsed = parse_nc_detail_html(detail_html)
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


def parse_nc_results_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    table = (
        soup.find("table", class_=re.compile(r"listspacing|results", re.I))
        or soup.find("table")
    )
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
            detail_url = f"https://www.sosnc.gov{detail_href}"
        else:
            detail_url = detail_href
        number = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        status = cells[-1].get_text(strip=True) if len(cells) > 2 else ""
        if name and detail_url:
            results.append({
                "name": name,
                "number": number,
                "status": status,
                "detail_url": detail_url,
            })
    return results


def parse_nc_detail_html(html: str) -> dict:
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
    h1 = soup.find("h1")
    if h1:
        out["entity_name"] = h1.get_text(strip=True)
    for label in soup.find_all(["th", "dt", "label"]):
        text = label.get_text(strip=True).lower()
        value_elem = label.find_next_sibling(["td", "dd", "span"])
        if not value_elem:
            continue
        value = value_elem.get_text(strip=True)
        if "sosid" in text or "filing number" in text:
            out["filing_number"] = value
        elif "status" in text and out["status"] is None:
            out["status"] = value
        elif "date formed" in text or "date of formation" in text:
            out["filing_date"] = value
    out["officers"] = _extract_officers_nc(soup)
    out["registered_agent"] = _extract_registered_agent_nc(soup)
    out["principal_address"] = _extract_principal_address_nc(soup)
    return out


def _extract_officers_nc(soup) -> list[dict]:
    header = soup.find(
        lambda t: t.name in ("h2", "h3", "th")
        and "company official" in t.get_text(strip=True).lower()
    )
    if not header:
        return []
    table = header.find_next("table")
    if not table:
        return []
    officers: list[dict] = []
    rows = table.find_all("tr")
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(strip=True)
        name = cells[1].get_text(strip=True)
        address = None
        if len(cells) > 2:
            addr_text = cells[2].get_text("\n", strip=True)
            addr_lines = [line for line in addr_text.split("\n") if line.strip()]
            address = _parse_address_lines_nc(addr_lines)
        if name:
            officers.append({"title": title, "name": name, "address": address})
    return officers


def _extract_registered_agent_nc(soup) -> Optional[dict]:
    header = soup.find(
        lambda t: t.name in ("h2", "h3", "th", "label")
        and "registered agent" in t.get_text(strip=True).lower()
    )
    if not header:
        return None
    container = header.find_next("table") or header.find_next("div")
    if not container:
        return None
    text = container.get_text("\n", strip=True)
    lines = [
        line for line in text.split("\n")
        if line.strip() and "registered agent" not in line.lower()
    ]
    if not lines:
        return None
    return {"name": lines[0], "address": _parse_address_lines_nc(lines[1:])}


def _extract_principal_address_nc(soup) -> Optional[dict]:
    header = soup.find(
        lambda t: t.name in ("h2", "h3", "th", "label")
        and "principal office" in t.get_text(strip=True).lower()
    )
    if not header:
        return None
    container = header.find_next("table") or header.find_next("div")
    if not container:
        return None
    text = container.get_text("\n", strip=True)
    lines = [
        line for line in text.split("\n")
        if line.strip() and "principal" not in line.lower()
    ]
    return _parse_address_lines_nc(lines)


def _parse_address_lines_nc(lines: list[str]) -> Optional[dict]:
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
