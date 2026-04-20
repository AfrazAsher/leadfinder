
I'm building Stage 3a (SOS Lookup — FL + NC scrapers) for LeadFinder — a pipeline that takes LLC property ownership records and enriches them with decision-maker contact info. Stages 0, 2, and 1 are already shipped and committed (34 tests passing).

This prompt swaps out the Stage 3 STUB with a real implementation covering two state Secretary of State portals: Florida Sunbiz and North Carolina SOS. It also adds a Provider protocol, a Playwright browser lifecycle manager, and a fall-forward chain (when state A's scraper fails or returns empty, try state B from `filing_state_candidates`). It does NOT include OpenCorporates, WA UBI, UT Business Search, or ownership chain resolution — those come in later stages.

## Environment context

- Project root: repo with `venv/` at the project root (Python 3.10)
- Activate venv: `venv\Scripts\activate` (Windows)
- Backend at `backend/`; existing tests must still pass (34 passing before your changes)
- Playwright is already in `pyproject.toml` runtime deps (added for Stage 0 even though unused until now)
- Playwright browser binaries need to be installed: run `playwright install chromium` (you'll need to do this once after pip install)

## Project context — read FIRST if anything below seems ambiguous

See `docs/design/` for full design context:

- `step_1_system_understanding.md` — scope, non-goals, edge cases
- `step_2_architecture.md` — in-memory state, single-process
- `step_3_pipeline_design.md` — pipeline flow, concurrency, caching
- `step_5_entity_resolution.md` — name matching thresholds (critical for Stage 3)
- `docs/prompts/stage_1_spec.md` — the HTTP+orchestrator contract
- `docs/prompts/stage_2_spec.md` — the parse_entity contract

Key principles that drive this stage:

1. **Primary sources, not aggregators.** We scrape state SOS sites directly.
2. **One shared browser.** Single Chromium instance for the whole run; scrapers take turns.
3. **Cooperative cancellation.** Orchestrator's cancellation checks must work inside scraper loops.
4. **Graceful degradation.** A scraper failure never kills the run — entity stays unenriched.
5. **Fall-forward chain.** If FL fails, try NC. No `OpenCorporates` fallback in 3a (that's deferred to 3c).

## Documentation updates required BEFORE implementation

These design docs reference OpenCorporates (OC) as a fallback source. Since OC is deferred to Stage 3c, remove OC references from the critical-path discussion. Do these FIRST, then implement code. Use str_replace / search-and-replace carefully.

### `docs/design/step_3_pipeline_design.md`

In §3.5 "Worked Example: ROLATOR", the pseudocode shows `# Final fallback: OpenCorporates`. Replace with:

```
# No OpenCorporates fallback in v1 — if all state scrapers fail,
# entity becomes `unenriched`. OC is deferred to Stage 3c as an
# optional last-resort fallback for rare cases where state scraping
# returns nothing for all candidate states.
```

In §3.7 "Caching Strategy" TTL table: remove the `OpenCorporates | 30 days` row.

In §3.8 "Quota Management": remove the `opencorporates` seed entry from the `init_quotas` call; keep only `serper`, `hunter`, `apollo`. (Quota code itself stays the same; only the seed values change.)

In §3.12 "Source Independence", remove OpenCorporates from the `SOS filings (ground truth)` tree. The tree should now look like:

```
SOS filings (ground truth)
    ├── FL Sunbiz (direct — Stage 3a)
    ├── NC SOS (direct — Stage 3a)
    ├── WA UBI (direct — Stage 3b)
    └── UT Business Search (direct — Stage 3b)
```

In §3.14 "What Step 3 Leaves to Later Steps", ADD a new bullet:

```
- OpenCorporates fallback (Stage 3c; optional, only if state scrapers show real coverage gaps)
```

### `docs/design/step_4_confidence_scoring.md`

In §4.5 "Source reliability baseline" table, remove the `OpenCorporates | 0.75` row.

In §4.6 "Independence multipliers", remove `opencorporates` from the `sos_aggregator` group. The group becomes unused in v1 but keep the code structure for 3c.

### `docs/design/step_5_entity_resolution.md`

In §5.6 "Cross-Source Reconciliation" example, remove the OC entry from the `entity.sos_results` list. Simplify the example to show only FL Sunbiz + LinkedIn.

### `backend/app/core/config.py`

Remove `opencorporates_api_token` from Settings. Add `headless: bool = Field(default=True)` — new setting for toggling headful mode during dev.

### `backend/app/state.py`

In `init_quotas`, remove the `"opencorporates"` entry. The final dict should only have `serper`, `hunter`, `apollo`.

After the doc + config changes, run `python -m pytest -v` to confirm nothing broke. Expected: 34 passing.

---

## What Stage 3a Builds

```
backend/app/providers/
├── __init__.py             NEW — empty
├── base.py                 NEW — Protocol for SOS providers
├── browser.py              NEW — Playwright browser lifecycle manager
├── sos_fl.py               NEW — Florida Sunbiz scraper
└── sos_nc.py               NEW — North Carolina SOS scraper

backend/app/pipeline/
└── stage_3_sos.py          REPLACE — real lookup_sos (was stub)

backend/tests/fixtures/sos/
├── __init__.py             NEW — empty
├── fl_results_rolator.html NEW — captured FL Sunbiz search results HTML
├── fl_detail_rolator.html  NEW — captured FL Sunbiz detail page HTML
├── fl_results_empty.html   NEW — captured FL "no results" page
├── nc_results_acme.html    NEW — captured NC SOS search results
├── nc_detail_acme.html     NEW — captured NC SOS detail page
└── nc_results_empty.html   NEW — NC "no results" page

backend/tests/
├── test_provider_base.py   NEW — protocol conformance tests
├── test_browser.py         NEW — browser lifecycle tests
├── test_sos_fl.py          NEW — FL scraper unit tests (HTML parsing)
├── test_sos_nc.py          NEW — NC scraper unit tests (HTML parsing)
└── test_stage_3_sos.py     NEW — integration tests for lookup_sos with mock providers
```

### Files NOT to touch

```
backend/app/pipeline/stage_0_cleaning.py
backend/app/pipeline/stage_2_parsing.py
backend/app/pipeline/stage_4_enrichment.py        (stub stays)
backend/app/pipeline/stage_5_output.py
backend/app/pipeline/orchestrator.py              (stays; no changes required)
backend/app/routers/*.py
backend/app/models/*.py
backend/tests/test_stage_0_cleaning.py
backend/tests/test_stage_2_parsing.py
backend/tests/test_stage_1_run.py
backend/tests/test_orchestrator.py
```

---

## 1. The Provider Protocol (base.py)

```python
# backend/app/providers/base.py
from typing import Protocol, Optional
from app.models.entity import CleanedEntity


class SOSProvider(Protocol):
    """Contract every state SOS scraper implements."""

    state_code: str  # e.g., "FL", "NC"
    display_name: str  # e.g., "Florida Sunbiz", "NC Secretary of State"

    async def search(
        self, entity: CleanedEntity
    ) -> Optional["SOSResult"]:
        """
        Search the state's SOS portal for the entity.
        Returns None if entity not found or all variants failed.
        Raises ScraperError only for unrecoverable problems (infra issues).
        """
        ...


class SOSResult:
    """Normalized output from any state SOS scraper. Plain dict under the hood."""
    def __init__(
        self,
        filing_number: str,
        entity_name: str,
        status: str,  # "Active", "Inactive", "Dissolved", etc.
        principal_address: Optional[dict] = None,  # {street, city, state, zip}
        mailing_address: Optional[dict] = None,
        registered_agent: Optional[dict] = None,  # {name, address}
        officers: Optional[list[dict]] = None,  # [{name, title, address}]
        filing_date: Optional[str] = None,
        source_url: Optional[str] = None,
    ):
        self.filing_number = filing_number
        self.entity_name = entity_name
        self.status = status
        self.principal_address = principal_address or {}
        self.mailing_address = mailing_address or {}
        self.registered_agent = registered_agent or {}
        self.officers = officers or []
        self.filing_date = filing_date
        self.source_url = source_url

    def to_dict(self) -> dict:
        return {
            "filing_number": self.filing_number,
            "entity_name": self.entity_name,
            "status": self.status,
            "principal_address": self.principal_address,
            "mailing_address": self.mailing_address,
            "registered_agent": self.registered_agent,
            "officers": self.officers,
            "filing_date": self.filing_date,
            "source_url": self.source_url,
        }


class ScraperError(Exception):
    """Infra-level failure (browser crash, DNS failure). Not a 'no result'."""
    pass


class ScraperBlocked(ScraperError):
    """Detected bot-check / CAPTCHA / rate-limit block. Stop; don't retry."""
    pass
```

## 2. Browser Lifecycle Manager (browser.py)

```python
# backend/app/providers/browser.py
import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
from app.core.config import settings


class BrowserManager:
    """
    Manages a single shared Chromium instance across a pipeline run.
    Scrapers obtain pages from it via `get_page()`; the manager serializes
    access via an asyncio.Lock so we never have more than one navigation
    happening on the same page at once.

    Usage:
        async with BrowserManager() as bm:
            async with bm.page_lock:
                page = await bm.get_page("fl_sunbiz")
                await page.goto(...)
                ...
    """

    def __init__(self, headless: Optional[bool] = None):
        self.headless = headless if headless is not None else settings.headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._pages: dict[str, object] = {}
        self.page_lock = asyncio.Lock()

    async def __aenter__(self):
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
        # Hide webdriver flag
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Close pages → context → browser → playwright, swallowing errors
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

    async def get_page(self, key: str):
        """Get or create a named page. Reused across entities for efficiency."""
        if key not in self._pages:
            assert self._context is not None
            page = await self._context.new_page()
            page.set_default_timeout(15000)  # 15s per action
            self._pages[key] = page
        return self._pages[key]
```

## 3. FL Sunbiz Scraper (sos_fl.py)

```python
# backend/app/providers/sos_fl.py
import re
from typing import Optional
from rapidfuzz import fuzz
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from app.models.entity import CleanedEntity
from app.providers.base import SOSProvider, SOSResult, ScraperError, ScraperBlocked
from app.providers.browser import BrowserManager
from app.state import AppState
from app.events import emit_log


SEARCH_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"
NAME_MATCH_THRESHOLD = 0.85  # Per step_5_entity_resolution.md


class FLSunbizProvider:
    state_code = "FL"
    display_name = "Florida Sunbiz"

    def __init__(self, state: AppState, browser: BrowserManager):
        self.state = state
        self.browser = browser

    async def search(self, entity: CleanedEntity) -> Optional[SOSResult]:
        """Try each search_name_variant until we find a high-confidence match."""
        async with self.browser.page_lock:
            page = await self.browser.get_page("fl_sunbiz")

            for variant in entity.search_name_variants[:5]:
                await emit_log(
                    self.state, "INFO",
                    f"[FL] Searching: {variant!r}"
                )
                try:
                    results = await self._search_variant(page, variant)
                except ScraperBlocked:
                    raise
                except (PlaywrightTimeoutError, Exception) as e:
                    await emit_log(
                        self.state, "WARN",
                        f"[FL] Variant {variant!r} failed: {type(e).__name__}"
                    )
                    continue

                best = self._pick_best_match(variant, results)
                if best:
                    detail = await self._fetch_detail(page, best["detail_url"])
                    return self._make_result(detail, best)

            return None

    async def _search_variant(self, page, variant: str) -> list[dict]:
        """Navigate to search page, submit form, parse result list."""
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")

        # Detect bot check (Sunbiz doesn't have one, but defensive)
        content = await page.content()
        if "captcha" in content.lower() or "cloudflare" in content.lower():
            raise ScraperBlocked("FL Sunbiz returned a challenge page")

        # Fill and submit
        await page.fill('input[name="SearchTerm"]', variant)
        await page.click('input[type="submit"][value="Search Now"]')
        await page.wait_for_load_state("domcontentloaded")

        # Parse results table
        return await self._parse_results_list(page)

    async def _parse_results_list(self, page) -> list[dict]:
        """Extract rows from the search results table."""
        html = await page.content()
        return parse_fl_results_html(html, page.url)

    async def _fetch_detail(self, page, url: str) -> str:
        """Navigate to detail page and return HTML."""
        await page.goto(url, wait_until="domcontentloaded")
        return await page.content()

    def _pick_best_match(self, search_name: str, results: list[dict]) -> Optional[dict]:
        """Return the highest-scored result that meets threshold."""
        if not results:
            return None
        scored = []
        for r in results:
            score = fuzz.token_sort_ratio(
                search_name.upper(), r["name"].upper()
            ) / 100.0
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score, top = scored[0]
        if top_score >= NAME_MATCH_THRESHOLD:
            return top
        return None

    def _make_result(self, detail_html: str, list_item: dict) -> SOSResult:
        parsed = parse_fl_detail_html(detail_html)
        return SOSResult(
            filing_number=parsed.get("filing_number", list_item.get("number", "")),
            entity_name=parsed.get("entity_name", list_item.get("name", "")),
            status=parsed.get("status", list_item.get("status", "Unknown")),
            principal_address=parsed.get("principal_address"),
            mailing_address=parsed.get("mailing_address"),
            registered_agent=parsed.get("registered_agent"),
            officers=parsed.get("officers", []),
            filing_date=parsed.get("filing_date"),
            source_url=list_item.get("detail_url"),
        )


# --- Pure-function HTML parsers (unit-testable without a browser) ---

def parse_fl_results_html(html: str, base_url: str = "") -> list[dict]:
    """
    Parse FL Sunbiz search results page. Return list of
    {name, number, status, detail_url}. Empty list means no results.

    IMPORTANT: This is a pure function — no Playwright required.
    Tests call this directly with fixture HTML files.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Sunbiz results table has class 'search-results' or similar;
    # rows have a link in the first cell pointing to detail page.
    # Structure (as of 2026): <div id="search-results"> → <table> → <tr>s
    # with 4 columns: Name | Number | Status | (empty)

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
    """
    Parse FL Sunbiz corporation detail page. Return normalized dict with
    filing_number, entity_name, status, addresses, registered_agent, officers.

    Structure is a series of <div class="detailSection"> blocks, each with
    a label and value. Officers are in a section titled "Authorized Person(s)
    Detail" or "Officer/Director Detail".
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = {
        "filing_number": None,
        "entity_name": None,
        "status": None,
        "principal_address": None,
        "mailing_address": None,
        "registered_agent": None,
        "officers": [],
        "filing_date": None,
    }

    # Entity name: first <span class="detailSectionTitleLabel"> or <p> with big text
    title_span = soup.find("span", class_=re.compile(r"entityName|title", re.I))
    if title_span:
        out["entity_name"] = title_span.get_text(strip=True)

    # Look for all detailSection blocks and match by label text
    for section in soup.find_all("div", class_=re.compile(r"detailSection")):
        label_elem = section.find(["label", "span", "h3"])
        if not label_elem:
            continue
        label = label_elem.get_text(strip=True).lower()

        if "document number" in label:
            value = section.get_text(strip=True).replace(label_elem.get_text(strip=True), "").strip()
            out["filing_number"] = value
        elif "status" in label and out["status"] is None:
            value = section.get_text(strip=True).replace(label_elem.get_text(strip=True), "").strip()
            out["status"] = value
        elif "filing date" in label or "date filed" in label:
            value = section.get_text(strip=True).replace(label_elem.get_text(strip=True), "").strip()
            out["filing_date"] = value

    # Principal / mailing addresses: parse address blocks
    out["principal_address"] = _extract_address_block(soup, "principal")
    out["mailing_address"] = _extract_address_block(soup, "mailing")

    # Registered agent
    out["registered_agent"] = _extract_registered_agent(soup)

    # Officers / Authorized Persons
    out["officers"] = _extract_officers_fl(soup)

    return out


def _extract_address_block(soup, kind: str) -> Optional[dict]:
    """Extract {street, city, state, zip} from a named address block."""
    # FL detail pages have <div> blocks with headers like "Principal Address"
    header = soup.find(
        lambda tag: tag.name in ("h3", "span", "label")
        and kind.lower() in tag.get_text(strip=True).lower()
    )
    if not header:
        return None
    # Find the next element with address text
    container = header.find_parent("div") or header.find_next_sibling("div")
    if not container:
        return None
    lines = [
        line.strip()
        for line in container.get_text("\n", strip=True).split("\n")
        if line.strip() and kind.lower() not in line.lower()
    ]
    if len(lines) < 2:
        return None
    return parse_address_lines(lines)


def _extract_registered_agent(soup) -> Optional[dict]:
    """Find 'Registered Agent Name & Address' block."""
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
        "name": lines[0] if lines else None,
        "address": parse_address_lines(lines[1:]) if len(lines) > 1 else None,
    }


def _extract_officers_fl(soup) -> list[dict]:
    """Find 'Authorized Person(s) Detail' or 'Officer/Director Detail' section."""
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
    officers = []
    # Each officer block has title (e.g. "MGR"), name, address
    # Pattern: repeating <div> or <span> pairs of "Title" followed by name
    for block in section.find_all(
        lambda t: t.name == "div" and t.find_all(text=re.compile(r"(MGR|MGRM|AMBR|PRES|VP|DIRECTOR|TRUSTEE|MEMBER)", re.I))
    ):
        text = block.get_text("\n", strip=True)
        lines = [l for l in text.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        # Title is usually the first line; name the second; rest is address
        title = lines[0]
        name = lines[1]
        address = parse_address_lines(lines[2:]) if len(lines) > 2 else None
        officers.append({
            "title": title.strip(),
            "name": name.strip(),
            "address": address,
        })
    return officers


def parse_address_lines(lines: list[str]) -> Optional[dict]:
    """Parse 2-3 line US address into {street, city, state, zip}."""
    if not lines:
        return None
    # Last line should be "CITY, ST ZIP" format
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
```

## 4. NC SOS Scraper (sos_nc.py)

Very similar shape to FL but with NC-specific URL, form fields, and officer section name. Key differences:

- Search URL: `https://www.sosnc.gov/online_services/search/by_title/_Business_Registration`
- Search form uses a GET request with `Words=<name>` query parameter
- Results table uses class `listspacing` (empirically)
- Detail page section is "Company Officials" not "Authorized Persons"
- Officer title abbreviations are full words: "Manager", "Member" (not "MGR", "MGRM")

```python
# backend/app/providers/sos_nc.py
import re
from typing import Optional
from urllib.parse import quote_plus
from rapidfuzz import fuzz
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from app.models.entity import CleanedEntity
from app.providers.base import SOSProvider, SOSResult, ScraperError, ScraperBlocked
from app.providers.browser import BrowserManager
from app.state import AppState
from app.events import emit_log


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
                        f"[NC] Variant {variant!r} failed: {type(e).__name__}"
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
            score = fuzz.token_sort_ratio(
                search_name.upper(), r["name"].upper()
            ) / 100.0
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score, top = scored[0]
        if top_score >= NAME_MATCH_THRESHOLD:
            return top
        return None

    def _make_result(self, detail_html: str, list_item: dict) -> SOSResult:
        parsed = parse_nc_detail_html(detail_html)
        return SOSResult(
            filing_number=parsed.get("filing_number", list_item.get("number", "")),
            entity_name=parsed.get("entity_name", list_item.get("name", "")),
            status=parsed.get("status", list_item.get("status", "Unknown")),
            principal_address=parsed.get("principal_address"),
            mailing_address=parsed.get("mailing_address"),
            registered_agent=parsed.get("registered_agent"),
            officers=parsed.get("officers", []),
            filing_date=parsed.get("filing_date"),
            source_url=list_item.get("detail_url"),
        )


def parse_nc_results_html(html: str) -> list[dict]:
    """Parse NC SOS search results. Return list of {name, number, status, detail_url}."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    # NC results are in a table — each row has entity name (link), id, type, status
    table = soup.find("table", class_=re.compile(r"listspacing|results", re.I)) or soup.find("table")
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
        # Column layout: Name | SOSID | Type | Status (not all pages consistent)
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
    """Parse NC SOS detail page."""
    soup = BeautifulSoup(html, "html.parser")
    out = {
        "filing_number": None,
        "entity_name": None,
        "status": None,
        "principal_address": None,
        "mailing_address": None,
        "registered_agent": None,
        "officers": [],
        "filing_date": None,
    }
    # NC detail pages use <dl>/<dt>/<dd> or <table> with labels
    # Extract name from h1 or title
    h1 = soup.find("h1")
    if h1:
        out["entity_name"] = h1.get_text(strip=True)
    # SOS ID / Filing Number: look for "SOSID" label
    for label in soup.find_all(["th", "dt", "label"]):
        text = label.get_text(strip=True).lower()
        # Get adjacent value element
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
    # Company Officials section → officers
    out["officers"] = _extract_officers_nc(soup)
    # Registered agent
    out["registered_agent"] = _extract_registered_agent_nc(soup)
    # Principal office address
    out["principal_address"] = _extract_principal_address_nc(soup)
    return out


def _extract_officers_nc(soup) -> list[dict]:
    """NC: Company Officials section has rows with Title | Name | Address."""
    header = soup.find(
        lambda t: t.name in ("h2", "h3", "th") and "company official" in t.get_text(strip=True).lower()
    )
    if not header:
        return []
    table = header.find_next("table")
    if not table:
        return []
    officers = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(strip=True)
        name = cells[1].get_text(strip=True)
        address = None
        if len(cells) > 2:
            addr_text = cells[2].get_text("\n", strip=True)
            addr_lines = [l for l in addr_text.split("\n") if l.strip()]
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
    lines = [l for l in text.split("\n") if l.strip() and "registered agent" not in l.lower()]
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
    lines = [l for l in text.split("\n") if l.strip() and "principal" not in l.lower()]
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
```

## 5. New stage_3_sos.py (Replaces the Stub)

```python
# backend/app/pipeline/stage_3_sos.py
from typing import Optional
from app.models.entity import CleanedEntity
from app.state import AppState
from app.events import emit_log
from app.providers.base import SOSProvider, SOSResult, ScraperError, ScraperBlocked
from app.providers.browser import BrowserManager
from app.providers.sos_fl import FLSunbizProvider
from app.providers.sos_nc import NCSOSProvider


# Registry of scrapers keyed by state code. Stage 3b adds WA, UT.
def build_providers(state: AppState, browser: BrowserManager) -> dict[str, SOSProvider]:
    return {
        "FL": FLSunbizProvider(state, browser),
        "NC": NCSOSProvider(state, browser),
    }


async def lookup_sos(state: AppState, entity: CleanedEntity) -> None:
    """
    Try each state in entity.filing_state_candidates in order.
    Stop at first successful match. Leave entity.sos_results / sos_source
    populated for downstream stages.
    """
    browser: BrowserManager = state.cache.get("__browser__")  # type: ignore
    if browser is None:
        # Orchestrator forgot to open a browser — should not happen
        await emit_log(state, "ERROR", "No browser instance available for SOS lookup")
        return

    providers = build_providers(state, browser)
    for candidate_state in entity.filing_state_candidates:
        provider = providers.get(candidate_state)
        if not provider:
            await emit_log(
                state, "INFO",
                f"[SOS] No scraper for {candidate_state} yet; skipping"
            )
            continue
        try:
            result: Optional[SOSResult] = await provider.search(entity)
        except ScraperBlocked as e:
            await emit_log(state, "WARN", f"[SOS {candidate_state}] Blocked: {e}")
            continue
        except ScraperError as e:
            await emit_log(state, "ERROR", f"[SOS {candidate_state}] Error: {e}")
            continue
        except Exception as e:
            await emit_log(
                state, "ERROR",
                f"[SOS {candidate_state}] Unexpected: {type(e).__name__}: {e}"
            )
            continue

        if result:
            entity.sos_results = [result.to_dict()]
            entity.sos_source = f"{candidate_state.lower()}_direct"
            await emit_log(
                state, "INFO",
                f"[SOS {candidate_state}] Found: {result.entity_name} "
                f"(#{result.filing_number}, status={result.status}, "
                f"{len(result.officers)} officers)"
            )
            return

    # Nothing found in any state
    await emit_log(
        state, "INFO",
        f"[SOS] No match for {entity.entity_name_search!r} in "
        f"{entity.filing_state_candidates}"
    )
```

## 6. Orchestrator Integration

The orchestrator already calls `lookup_sos(state, entity)`. We just need to wrap the orchestrator's main loop in a `BrowserManager` async context. Update `backend/app/pipeline/orchestrator.py`:

Find this section near the top of `orchestrate()`:

```python
    try:
        await emit_log(state, "INFO", f"Orchestrator started for run {state.current_run.run_id}")

        semaphore = asyncio.Semaphore(settings.max_parallel_entities)
```

Replace with:

```python
    try:
        await emit_log(state, "INFO", f"Orchestrator started for run {state.current_run.run_id}")

        # Open shared browser for the whole run. Stored in cache under a
        # reserved key so stages can fetch it. Closed automatically on exit.
        from app.providers.browser import BrowserManager
        async with BrowserManager() as browser:
            state.cache["__browser__"] = browser
            try:
                semaphore = asyncio.Semaphore(settings.max_parallel_entities)
                # ... [rest of orchestrator body stays the same] ...
            finally:
                state.cache.pop("__browser__", None)
```

Ensure the full existing body (process_one, gather, Stage 5 output, done broadcast, etc.) is wrapped inside the `async with BrowserManager()` block. The `state.cache.pop` in finally ensures no stale reference if BrowserManager exits unexpectedly.

**Important:** the browser open/close is ONE PER RUN, not one per entity. Do not move it inside `process_one()`.

## 7. Dependencies

Add to `pyproject.toml` runtime dependencies:

- `beautifulsoup4>=4.12` (HTML parsing)
- `rapidfuzz>=3.6` (fuzzy name matching)

Verify playwright is already listed (it is from Stage 0 spec).

After `pip install -e ".[dev]"`, run `playwright install chromium` to install browser binaries.

## 8. Fixture HTML Files

We need captured HTML for each parser to test against without hitting the live internet. You will need to CREATE these fixture files, but since you can't actually browse the web:

**Use these MINIMAL synthetic fixtures** (not real captures — they exercise the parser contract only). Save each to `backend/tests/fixtures/sos/`.

### `fl_results_rolator.html`

```html
<html>
  <body>
    <table>
      <tr>
        <th>Entity Name</th>
        <th>Document Number</th>
        <th>Status</th>
        <th></th>
      </tr>
      <tr>
        <td>
          <a
            href="/Inquiry/CorporationSearch/SearchResultDetail?inquirytype=EntityName&directionType=Initial&searchNameOrder=ROLATOR&aggregateId=flal-L15000123456-xxx"
            >ROLATOR &amp; INDEPENDENCE LLC</a
          >
        </td>
        <td>L15000123456</td>
        <td>Active</td>
        <td></td>
      </tr>
    </table>
  </body>
</html>
```

### `fl_detail_rolator.html`

```html
<html>
  <body>
    <span class="entityName">ROLATOR &amp; INDEPENDENCE LLC</span>

    <div class="detailSection"><label>Document Number</label>L15000123456</div>
    <div class="detailSection"><label>Status</label>Active</div>
    <div class="detailSection"><label>Date Filed</label>01/15/2015</div>

    <div class="detailSection">
      <h3>Principal Address</h3>
      <div>10967 GRINDSTONE MNR FRISCO, TX 75035</div>
    </div>

    <div class="detailSection">
      <h3>Mailing Address</h3>
      <div>10967 GRINDSTONE MNR FRISCO, TX 75035</div>
    </div>

    <div class="detailSection">
      <h3>Registered Agent Name &amp; Address</h3>
      <div>KONDRU, SIVARAMAIAH 10967 GRINDSTONE MNR FRISCO, TX 75035</div>
    </div>

    <div class="detailSection">
      <h3>Authorized Person(s) Detail</h3>
      <div>MGR KONDRU, SIVARAMAIAH 10967 GRINDSTONE MNR FRISCO, TX 75035</div>
    </div>
  </body>
</html>
```

### `fl_results_empty.html`

```html
<html>
  <body>
    <p>No results found for your search.</p>
  </body>
</html>
```

### `nc_results_acme.html`

```html
<html>
  <body>
    <table class="listspacing">
      <tr>
        <th>Name</th>
        <th>SOSID</th>
        <th>Status</th>
      </tr>
      <tr>
        <td>
          <a
            href="/online_services/search/Business_Registration_Results.aspx?SOSID=123456"
            >ACME HOLDINGS LLC</a
          >
        </td>
        <td>123456</td>
        <td>Current-Active</td>
      </tr>
    </table>
  </body>
</html>
```

### `nc_detail_acme.html`

```html
<html>
  <body>
    <h1>ACME HOLDINGS LLC</h1>

    <table>
      <tr>
        <th>SOSID</th>
        <td>123456</td>
      </tr>
      <tr>
        <th>Status</th>
        <td>Current-Active</td>
      </tr>
      <tr>
        <th>Date Formed</th>
        <td>3/10/2018</td>
      </tr>
    </table>

    <h2>Principal Office</h2>
    <table>
      <tr>
        <td>123 MAIN ST<br />CHARLOTTE, NC 28202</td>
      </tr>
    </table>

    <h2>Registered Agent</h2>
    <table>
      <tr>
        <td>JONES, ROBERT<br />100 AGENT WAY<br />RALEIGH, NC 27601</td>
      </tr>
    </table>

    <h2>Company Officials</h2>
    <table>
      <tr>
        <th>Title</th>
        <th>Name</th>
        <th>Address</th>
      </tr>
      <tr>
        <td>Manager</td>
        <td>SMITH, JANE</td>
        <td>456 PARK AVE<br />CHARLOTTE, NC 28202</td>
      </tr>
    </table>
  </body>
</html>
```

### `nc_results_empty.html`

```html
<html>
  <body>
    <p>No records found matching your search.</p>
  </body>
</html>
```

These synthetic fixtures are deliberately minimal. When you run the real scraper against live FL Sunbiz for the first time, the parser may need tweaking for edge cases. That's expected — we test the contract with synthetic HTML, then iterate on real sites.

## 9. Tests

### `test_provider_base.py` (4 tests)

```python
from app.providers.base import SOSProvider, SOSResult, ScraperError, ScraperBlocked


def test_sos_result_to_dict_roundtrip():
    r = SOSResult(
        filing_number="L15000123456",
        entity_name="ROLATOR & INDEPENDENCE LLC",
        status="Active",
        officers=[{"name": "KONDRU, SIVARAMAIAH", "title": "MGR"}],
    )
    d = r.to_dict()
    assert d["filing_number"] == "L15000123456"
    assert d["entity_name"] == "ROLATOR & INDEPENDENCE LLC"
    assert len(d["officers"]) == 1


def test_sos_result_defaults():
    r = SOSResult(filing_number="X", entity_name="Y", status="Z")
    assert r.officers == []
    assert r.principal_address == {}


def test_scraper_error_hierarchy():
    assert issubclass(ScraperBlocked, ScraperError)


def test_scraper_blocked_is_exception():
    try:
        raise ScraperBlocked("test")
    except Exception:
        pass  # Must be catchable as Exception
```

### `test_browser.py` (2 tests — marked slow; only run when Playwright installed)

```python
import pytest
from app.providers.browser import BrowserManager


@pytest.mark.asyncio
@pytest.mark.slow
async def test_browser_opens_and_closes():
    async with BrowserManager(headless=True) as bm:
        page = await bm.get_page("test")
        await page.goto("about:blank")
        assert page.url == "about:blank"
    # After context exit, no pages should remain open


@pytest.mark.asyncio
@pytest.mark.slow
async def test_browser_reuses_named_pages():
    async with BrowserManager(headless=True) as bm:
        p1 = await bm.get_page("shared")
        p2 = await bm.get_page("shared")
        assert p1 is p2
```

Add `slow` marker registration to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["slow: browser-driving tests (require Playwright installed)"]
```

By default these run. To skip: `pytest -m "not slow"`.

### `test_sos_fl.py` (5 tests — pure HTML parsing, no browser)

```python
from pathlib import Path
from app.providers.sos_fl import parse_fl_results_html, parse_fl_detail_html, parse_address_lines

FIXTURES = Path(__file__).parent / "fixtures" / "sos"


def test_fl_results_parses_rolator():
    html = (FIXTURES / "fl_results_rolator.html").read_text()
    results = parse_fl_results_html(html)
    assert len(results) == 1
    assert "ROLATOR" in results[0]["name"]
    assert results[0]["number"] == "L15000123456"
    assert results[0]["status"] == "Active"
    assert "SearchResultDetail" in results[0]["detail_url"]


def test_fl_results_empty_returns_empty_list():
    html = (FIXTURES / "fl_results_empty.html").read_text()
    assert parse_fl_results_html(html) == []


def test_fl_detail_parses_rolator():
    html = (FIXTURES / "fl_detail_rolator.html").read_text()
    parsed = parse_fl_detail_html(html)
    assert parsed["entity_name"] == "ROLATOR & INDEPENDENCE LLC"
    assert parsed["filing_number"] == "L15000123456"
    assert parsed["status"] == "Active"
    assert len(parsed["officers"]) >= 1
    officer = parsed["officers"][0]
    assert "KONDRU" in officer["name"]


def test_fl_detail_extracts_principal_address():
    html = (FIXTURES / "fl_detail_rolator.html").read_text()
    parsed = parse_fl_detail_html(html)
    assert parsed["principal_address"] is not None
    assert parsed["principal_address"]["state"] == "TX"
    assert parsed["principal_address"]["zip"] == "75035"


def test_parse_address_lines_standard_us():
    result = parse_address_lines(["10967 GRINDSTONE MNR", "FRISCO, TX 75035"])
    assert result["street"] == "10967 GRINDSTONE MNR"
    assert result["city"] == "FRISCO"
    assert result["state"] == "TX"
    assert result["zip"] == "75035"
```

### `test_sos_nc.py` (4 tests)

```python
from pathlib import Path
from app.providers.sos_nc import parse_nc_results_html, parse_nc_detail_html

FIXTURES = Path(__file__).parent / "fixtures" / "sos"


def test_nc_results_parses_acme():
    html = (FIXTURES / "nc_results_acme.html").read_text()
    results = parse_nc_results_html(html)
    assert len(results) == 1
    assert results[0]["name"] == "ACME HOLDINGS LLC"
    assert results[0]["number"] == "123456"


def test_nc_results_empty_returns_empty_list():
    html = (FIXTURES / "nc_results_empty.html").read_text()
    assert parse_nc_results_html(html) == []


def test_nc_detail_parses_acme():
    html = (FIXTURES / "nc_detail_acme.html").read_text()
    parsed = parse_nc_detail_html(html)
    assert parsed["entity_name"] == "ACME HOLDINGS LLC"
    assert parsed["filing_number"] == "123456"
    assert parsed["status"] == "Current-Active"
    assert len(parsed["officers"]) >= 1
    assert parsed["officers"][0]["name"] == "SMITH, JANE"
    assert parsed["officers"][0]["title"] == "Manager"


def test_nc_detail_extracts_principal_address():
    html = (FIXTURES / "nc_detail_acme.html").read_text()
    parsed = parse_nc_detail_html(html)
    assert parsed["principal_address"] is not None
    assert parsed["principal_address"]["state"] == "NC"
```

### `test_stage_3_sos.py` (3 tests with mocked providers)

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.models.entity import CleanedEntity, EntityType, MailingAddress, SourceParcel
from app.models.run import RunState
from app.state import AppState, init_quotas
from app.pipeline.stage_3_sos import lookup_sos
from app.providers.base import SOSResult


def _make_entity(filing_states: list[str]):
    return CleanedEntity(
        entity_name_raw="TEST LLC",
        entity_name_cleaned="TEST LLC",
        entity_name_normalized="TEST LLC",
        entity_name_search="TEST LLC",
        search_name_variants=["TEST LLC", "TEST"],
        entity_type=EntityType.LLC,
        mailing_address=MailingAddress(state=filing_states[0]),
        source_parcels=[SourceParcel(apn="APN-1", property_state=filing_states[0])],
        filing_state_candidates=filing_states,
    )


def _make_state():
    s = AppState()
    init_quotas(s)
    s.current_run = RunState(
        run_id="test123",
        status="running",
        started_at=datetime.now(timezone.utc),
        current_stage="sos_lookup",
        entities_total=1,
    )
    return s


@pytest.mark.asyncio
async def test_lookup_sos_falls_forward_when_first_state_empty(monkeypatch):
    state = _make_state()
    browser = MagicMock()
    state.cache["__browser__"] = browser
    entity = _make_entity(["FL", "NC"])

    fl_provider = MagicMock(state_code="FL", display_name="FL")
    fl_provider.search = AsyncMock(return_value=None)
    nc_provider = MagicMock(state_code="NC", display_name="NC")
    nc_provider.search = AsyncMock(return_value=SOSResult(
        filing_number="X", entity_name="TEST LLC", status="Active",
        officers=[{"name": "JANE DOE", "title": "Manager"}]
    ))

    def fake_build(s, b):
        return {"FL": fl_provider, "NC": nc_provider}

    monkeypatch.setattr("app.pipeline.stage_3_sos.build_providers", fake_build)

    await lookup_sos(state, entity)
    assert entity.sos_source == "nc_direct"
    assert len(entity.sos_results) == 1
    assert entity.sos_results[0]["entity_name"] == "TEST LLC"


@pytest.mark.asyncio
async def test_lookup_sos_empty_when_no_states_match(monkeypatch):
    state = _make_state()
    state.cache["__browser__"] = MagicMock()
    entity = _make_entity(["FL", "NC"])

    fl_provider = MagicMock(state_code="FL")
    fl_provider.search = AsyncMock(return_value=None)
    nc_provider = MagicMock(state_code="NC")
    nc_provider.search = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.pipeline.stage_3_sos.build_providers",
        lambda s, b: {"FL": fl_provider, "NC": nc_provider}
    )

    await lookup_sos(state, entity)
    assert entity.sos_source is None
    assert entity.sos_results == []


@pytest.mark.asyncio
async def test_lookup_sos_skips_states_without_scraper(monkeypatch):
    state = _make_state()
    state.cache["__browser__"] = MagicMock()
    entity = _make_entity(["TX", "DE"])  # No scraper for either in 3a

    monkeypatch.setattr("app.pipeline.stage_3_sos.build_providers", lambda s, b: {})

    await lookup_sos(state, entity)
    # Nothing found, entity stays empty
    assert entity.sos_results == []
    assert entity.sos_source is None
```

---

## Acceptance Criteria

1. `cd backend && pip install -e ".[dev]"` works (beautifulsoup4 + rapidfuzz installed)
2. `playwright install chromium` runs successfully (one-time)
3. `python -m pytest -v` — ALL tests pass. Expected: previous 34 + ~18 new = **~52 tests passing**.
4. Browser-dependent tests in `test_browser.py` pass when run. If `pytest -m "not slow"`, they're skipped.
5. `uvicorn app.main:app --reload` still starts cleanly.
6. Manual smoke test (OPTIONAL — won't be part of your verification):
   - Upload `backend/tests/fixtures/sample_input.csv`
   - Because the fixture entities have filing_state_candidates that include non-FL/NC states (TX, MD, ID, CA, DE), they'll mostly skip the scrapers and remain unenriched — that's correct behavior.
   - The output CSV still generates; entities are unenriched but log lines show scraper attempts happening.

## What NOT to Do

- DO NOT touch Stage 0 code, Stage 2 code, Stage 4 stub, Stage 5 code, routers, or main.py wiring
- DO NOT add OpenCorporates / OC provider — deferred to Stage 3c
- DO NOT add WA UBI or UT Business Search scrapers — deferred to Stage 3b
- DO NOT implement ownership chain resolution — deferred to Stage 3b
- DO NOT attempt to fetch live fixture HTML from real state websites during your session (you don't have internet access that way — use the synthetic fixtures provided)
- DO NOT add retry logic via tenacity in 3a — retries come in Stage 4 where more volume means they matter more
- DO NOT add confidence scoring — Stage 5 handles that
- DO NOT change the CleanedEntity model — runtime fields are already there from Stage 1

## Before You Code

ASK clarifying questions if ANY rule is ambiguous. Likely questions and preferred answers:

- **"What if Stage 0/2 tests break after I modify stage_5_output.py?"**
  → You shouldn't be modifying stage_5_output.py. If you find yourself wanting to, stop and ask.

- **"The synthetic HTML fixtures don't match real FL Sunbiz output. Real parsing might differ."**
  → Correct. That's expected. The synthetic fixtures test the parser contract (given HTML with shape X, parser produces result Y). Real-site differences will surface during manual smoke testing — which I'll do after this ships. We'll iterate the parser to handle real HTML then. For now, just make the synthetic tests pass.

- **"Should the browser open ONCE for the whole run or per-entity?"**
  → ONCE per run. In the orchestrator. Not per entity. This matters — don't get it wrong.

- **"What about Playwright's browser_context() cleanup on cancellation?"**
  → Using `async with BrowserManager()` ensures cleanup. If asyncio.CancelledError propagates up, the context manager's `__aexit__` runs and closes everything.

- **"Can I refactor orchestrator.py beyond adding the BrowserManager wrapper?"**
  → No. Only the minimum change needed to wrap the main body in `async with BrowserManager()`. Keep everything else identical.

Show me your plan (files + one-line description each) and clarifying questions. Wait for my explicit "approved" before writing code. If any spec detail contradicts existing code or doesn't make sense, point it out.
