I'm building LeadFinder — a tool that takes a CSV of property ownership records and enriches LLC entities with decision-maker contact info. Set up the FOUNDATION only — do NOT build the whole thing. This is step 1 of a multi-step build.

## Environment context (important — read first)

- I'm running in an EMPTY project directory that contains one existing item: a Python venv at `./venv/`
- The venv is Python 3.10+ (already verified)
- DO NOT create a new venv. DO NOT use uv, poetry, pipenv, or conda. Use plain pip + pyproject.toml.
- Dependencies should be installable with: `pip install -e ".[dev]"` from the `backend/` directory, AFTER activating the user's existing `./venv/`
- My OS: (Claude Code, detect from shell) — use cross-platform paths and commands where possible

## Tech stack (LOCKED — do not suggest alternatives)

- Backend: Python 3.10 + FastAPI (async)
- Package management: pip + pyproject.toml (PEP 621 style)
- Database: Supabase Postgres (NOT connected yet — we add later)
- File storage: Supabase Storage (NOT connected yet)
- Frontend: Next.js 14 + shadcn/ui + Tailwind (NOT in this step — leave frontend/ as .gitkeep)
- Hosting: Railway (backend) + Supabase (DB/storage)
- Browser automation: Playwright Python (install as dependency; no scrapers yet)
- Logging: structlog
- Config: pydantic-settings v2
- Testing: pytest + pytest-asyncio
- Linting: ruff (no black, no flake8)

## What to build in THIS step

Working Stage 0 (Data Cleaning) module with full test coverage, project scaffolding, Dockerfile, and a `/health` FastAPI endpoint. Nothing else.

### 1. Repository structure

Because the venv already exists at `./venv/`, build EVERYTHING ELSE alongside it:

```
leadfinder/                          # <-- you are here, venv/ already exists
├── venv/                            # EXISTING — do not touch
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   └── logging.py
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── entity.py
│   │   │   └── report.py
│   │   ├── pipeline/
│   │   │   ├── __init__.py
│   │   │   └── stage_0_cleaning.py
│   │   └── routers/
│   │       ├── __init__.py
│   │       └── health.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── fixtures/
│   │   │   └── sample_input.csv
│   │   └── test_stage_0_cleaning.py
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   └── .gitkeep
├── docs/
│   └── .gitkeep
├── docker-compose.yml
├── .gitignore
├── .dockerignore
└── README.md
```

### 2. pyproject.toml requirements

- Project name: `leadfinder-backend`
- Python: `>=3.10`
- Runtime dependencies: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings>=2`, `structlog`, `pandas`, `python-multipart`
- Optional dev group (`[dev]`): `pytest`, `pytest-asyncio`, `httpx`, `ruff`
- Playwright: INCLUDE `playwright` in runtime deps but note that browser binaries are installed by the Dockerfile (`playwright install chromium`), not by pip
- Build system: setuptools (via `[build-system]` with `requires = ["setuptools>=68", "wheel"]` and `build-backend = "setuptools.build_meta"`)
- Package discovery: use setuptools' auto-discovery via `[tool.setuptools.packages.find]` with `where = ["."]` and `include = ["app*"]`, so `app/` is importable after `pip install -e .`

### 3. Stage 0: Data Cleaning Logic

Input: path to a CSV file.
Output: a `CleaningReport` Pydantic object.

**COLUMN HANDLING:**

Read CSV with pandas. Normalize column headers on read: `strip()` + `replace(' ', '_')` + `upper()`. Map all the variations below to canonical internal names:

| Accepted input header (case/space insensitive) | Canonical internal name |
| ---------------------------------------------- | ----------------------- |
| FIRST NAME / FIRST_NAME                        | first_name              |
| LAST NAME / LAST_NAME                          | last_name               |
| OWNER_NAME_1 / OWNER NAME 1                    | owner_name              |
| MAILING ADDRESS / MAILING_ADDRESS              | mailing_address         |
| MAILING CITY / MAILING_CITY                    | mailing_city            |
| MAILING STATE / MAILING_STATE                  | mailing_state           |
| MAILING ZIP / MAILING_ZIP                      | mailing_zip             |
| PROPERTY ADDRESS / PROPERTY_ADDRESS            | property_address        |
| PROPERTY CITY / PROPERTY_CITY                  | property_city           |
| PROPERTY STATE / PROPERTY_STATE                | property_state          |
| PROPERTY ZIP / PROPERTY_ZIP                    | property_zip            |
| APN                                            | apn                     |
| COUNTY                                         | county                  |
| PRIORITY                                       | priority                |

Drop any other columns silently.

**Fail fast with a clear `ValueError` if any of these canonical columns are missing:** `owner_name`, `mailing_state`, `property_state`, `apn`.

**ZIP normalization:** zips come as floats from pandas (e.g., `75035.0`). Convert to 5-digit zero-padded string. Blank/NaN zip → `None`.

**CLASSIFICATION RULES (applied in this exact order, BEFORE dedup):**

Define:

- `ENTITY_SUFFIXES = {"LLC", "LLLP", "LP", "INC", "CORP", "LTD", "TRUST", "FARMS"}` (uppercase)
- `BUSINESS_KEYWORDS = {"LLC", "INC", "CORP", "LTD", "LP", "LLLP", "TRUST", "COMPANY", "FARMS", "HOLDINGS", "INVESTMENTS", "PARTNERS", "ENTERPRISES", "FOUNDATION", "PARTNERSHIP", "GROUP", "PROPERTIES", "ASSOCIATES"}`

Classification logic, in order:

```
owner = row.owner_name (the canonical field)
owner_upper = owner.upper().strip() if owner else ""

# 1. Data error — blank owner
if not owner_upper:
    skip → data_error_skipped

# 2. Sentinel
if matches_sentinel(owner_upper):
    skip → sentinel_skipped

# 3. Government
if has_government_keyword(owner_upper):
    skip → government_skipped

# 4. Religious
if has_religious_keyword(owner_upper):
    skip → religious_skipped

# 5. Probate
if has_probate_keyword(owner_upper):
    skip → probate_skipped

# 6. Swap-bug (entity suffix in FIRST NAME, name in LAST NAME)
first = row.first_name.upper().strip() if row.first_name else ""
if first in ENTITY_SUFFIXES and row.last_name:
    skip → data_error_skipped

# 7. Individual (FIRST + LAST both filled, FIRST not an entity suffix)
if row.last_name and first not in ENTITY_SUFFIXES:
    skip → individuals_skipped

# 8. Misclassified individual
# Only applies when LAST NAME is blank
if not row.last_name:
    tokens = owner_upper.split()
    if 2 <= len(tokens) <= 3 and not any(kw in owner_upper.split() for kw in BUSINESS_KEYWORDS):
        # Check whole-word match, not substring (avoid matching INCORP within INCORPORATED, etc.)
        skip → misclassified_individual_skipped

# 9. Keep as entity
keep
```

**Pattern definitions:**

`matches_sentinel(name)` → True if `name` contains any of these as substrings (case-insensitive): `"NOT AVAILABLE"`, `"UNKNOWN"`, `"SEE NOTES"`, `"WITHHELD"` — OR exactly equals `"N/A"` or `"NONE"` (whole match) — OR `len(name) < 3`.

`has_government_keyword(name)`: tokens in `{"COUNTY", "DISTRICT", "MUNICIPAL"}` appear as whole words, OR phrases `"STATE OF"`, `"CITY OF"`, `"TOWN OF"`, `"DEPARTMENT OF"`, `"PUBLIC UTILITY"` appear as word-boundary matches.

`has_religious_keyword(name)`: any of `{"CHURCH", "MINISTRY", "TEMPLE", "MOSQUE", "SYNAGOGUE", "DIOCESE", "PARISH", "CATHEDRAL"}` appears as a whole word.

`has_probate_keyword(name)`: `"ESTATE OF"`, `"DECEASED"`, `"PROBATE"` appear as substrings.

**IMPORTANT: `FOUNDATION` alone is NOT a skip signal.** `STOCKTON FOUNDATION INC` must be KEPT. FOUNDATION is only in the BUSINESS_KEYWORDS set for detecting misclassified individuals (rule 8).

**NAME NORMALIZATION (for kept entities, before dedup):**

Apply in order:

1. Strip leading/trailing whitespace
2. Collapse interior whitespace runs to single spaces (`"A  B"` → `"A B"`)
3. Normalize ampersand spacing: replace `r"\s*&\s*"` with `" & "` (both `A&B` and `A  &   B` become `A & B`)
4. Strip trailing commas
5. Normalize period-separated entity types using regex word-boundary matching: `L.L.C.` → `LLC`, `L.P.` → `LP`, `L.L.L.P.` → `LLLP`, `INC.` → `INC`, `CORP.` → `CORP`, `LTD.` → `LTD`

Produce two variants:

- `entity_name_cleaned` — original case preserved (for display)
- `entity_name_normalized` — uppercase version (for dedup key)

**CANONICAL NAME SELECTION:**

`owner_name` (from `OWNER_NAME_1`) is always the canonical source. Do NOT use `first_name`.

**TRUNCATION DETECTION:**

If `first_name` has length exactly 30 (upstream truncation signal), add `"truncated_source_name"` to the entity's quality_flags.

**ENTITY TYPE DETECTION:**

Look at tokens of `entity_name_normalized`. Check last token, then second-to-last:

- Ends with `LLC` → `LLC`
- Ends with `LLLP` → `LLLP`
- Ends with `LP` (and not `LLLP`) → `LP`
- Ends with `INC` or `CORPORATION` → `INC`
- Ends with `CORP` → `CORP`
- Ends with `LTD` → `LTD`
- Ends with `TRUST`, `TR`, `TRS` → `TRUST`
- Ends with `PARTNERSHIP` or `P/S` → `PARTNERSHIP`
- Otherwise → `OTHER`

**DEDUPLICATION:**

Group kept entities by `entity_name_normalized`. For each group:

- `source_parcels`: list containing all unique (apn, property_address, property_city, property_state, county) tuples from every row
- `mailing_address`: take the first non-null mailing address from the group (by CSV order)
- If different rows have different non-null (street, city, zip) tuples, add `"mailing_address_conflict"` to quality_flags
- `is_priority`: True if any row in the group has `priority == "Yes"` (case-insensitive)

**QUALITY FLAGS** (add to `quality_flags` list):

- `"cryptic_name"` — entity_name_cleaned length 3–8 AND no BUSINESS_KEYWORD found in the name
- `"truncated_source_name"` — original `first_name` was exactly 30 chars
- `"mailing_address_incomplete"` — any of street/city/state/zip is None or blank after parsing
- `"mailing_address_conflict"` — per above
- `"trust_with_legal_boilerplate"` — `entity_type == TRUST` AND (`"U/A"` or `"DTD"` appears in entity_name_normalized)

### 4. Pydantic Models

```python
# backend/app/models/entity.py
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class EntityType(str, Enum):
    LLC = "LLC"
    LP = "LP"
    LLLP = "LLLP"
    INC = "INC"
    CORP = "CORP"
    LTD = "LTD"
    TRUST = "TRUST"
    PARTNERSHIP = "PARTNERSHIP"
    OTHER = "OTHER"

class MailingAddress(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    complete: bool = False

class SourceParcel(BaseModel):
    apn: str
    property_address: Optional[str] = None
    property_city: Optional[str] = None
    property_state: str
    county: Optional[str] = None

class CleanedEntity(BaseModel):
    entity_id: UUID = Field(default_factory=uuid4)
    entity_name_raw: str
    entity_name_cleaned: str
    entity_name_normalized: str
    entity_name_search: Optional[str] = None
    entity_type: EntityType
    mailing_address: MailingAddress
    source_parcels: list[SourceParcel]
    filing_state_candidates: list[str] = []
    is_priority: bool = False
    quality_flags: list[str] = []
```

```python
# backend/app/models/report.py
from pydantic import BaseModel, computed_field
from .entity import CleanedEntity

class CleaningReport(BaseModel):
    input_rows: int
    unique_entities: int
    individuals_skipped: int
    government_skipped: int
    religious_skipped: int
    probate_skipped: int
    sentinel_skipped: int
    data_error_skipped: int
    misclassified_individual_skipped: int
    entities: list[CleanedEntity]
    skip_summary_text: str

    @computed_field
    @property
    def total_skipped(self) -> int:
        return (self.individuals_skipped + self.government_skipped +
                self.religious_skipped + self.probate_skipped +
                self.sentinel_skipped + self.data_error_skipped +
                self.misclassified_individual_skipped)
```

### 5. Fixture CSV (14 rows)

Create `backend/tests/fixtures/sample_input.csv` with EXACTLY this header and these rows. Keep quoting for fields containing commas. FIRST NAME column for row 9 must be EXACTLY 30 characters (`TREASURE VALLEY INVESTMENTS LL` — count: T-R-E-A-S-U-R-E space V-A-L-L-E-Y space I-N-V-E-S-T-M-E-N-T-S space L-L = 30).

Header:

```
FIRST NAME,LAST NAME,MAILING ADDRESS,MAILING CITY,MAILING STATE,MAILING ZIP,PROPERTY ADDRESS,PROPERTY CITY,PROPERTY STATE,PROPERTY ZIP,APN,COUNTY,OWNER_NAME_1,PRIORITY
```

Rows (use "" for blank — avoid literal NaN/null text):

```
ROLATOR & INDEPENDENCE LLC,,10967 GRINDSTONE MNR,FRISCO,TX,75035,INDEPENDENCE PKWY,FRISCO,TX,75035,R-10354-00A-0020-1,COLLIN,ROLATOR & INDEPENDENCE LLC,Yes
LLLP,GEMINI,1979 N LOCUST GROVE RD,MERIDIAN,ID,83646,W STATE ST,EAGLE,ID,83616,S0508244605,ADA,"GEMINI, LLLP",No
RICARDO,DIAZ,320 W BEAR CREEK RD,GLENN HEIGHTS,TX,75154,320 W BEAR CREEK RD,GLENN HEIGHTS,TX,75154,65120521510040000,DALLAS,"DIAZ, RICARDO",No
CLARK COUNTY,,4700 NE 78TH ST,VANCOUVER,WA,98665,5503 NE 119TH ST,VANCOUVER,WA,98686,199236-000,CLARK,CLARK COUNTY,No
SHILOH BAPTIST CHURCH,,3565 9TH AVE,SACRAMENTO,CA,95817,FLORIN RD,SACRAMENTO,CA,95824,APN-SAC-001,SACRAMENTO,SHILOH BAPTIST CHURCH,No
WUNDERLICH CLARICE A ESTATE OF,,20817 RHODES RD,SPRING,TX,77388,20817 RHODES RD,SPRING,TX,77388,APN-HAR-001,HARRIS,WUNDERLICH CLARICE A ESTATE OF,No
NOT AVAILABLE FROM THE DATA,,539 S 800 E,,ID,,,,WA,,986048-065,CLARK,NOT AVAILABLE FROM THE DATA,No
LEE WEN-CHI,,2829 MEADOWBROOK DR,PLANO,TX,75075,13664 COUNTY ROAD 426,ANNA,TX,75409,R-7011-000-0030-1,COLLIN,LEE WEN-CHI,No
TREASURE VALLEY INVESTMENTS LL,,140 W SKYLARK DR,BOISE,ID,83702,W COBALT DR,MERIDIAN,ID,83642,S1214233685,ADA,TREASURE VALLEY INVESTMENTS LLC,No
ROBERTSON CROSSING LLC,,506 MAIN ST # 300,GAITHERSBURG,MD,20878,1128 MASSEY FARM RD,KNIGHTDALE,NC,27545,APN-WAKE-01,WAKE,ROBERTSON CROSSING LLC,No
ROBERTSON CROSSING LLC,,506 MAIN ST # 300,GAITHERSBURG,MD,20878,ROBERTSON ST,,NC,27545,APN-WAKE-02,WAKE,ROBERTSON CROSSING LLC,No
ROBERTSON CROSSING LLC,,506 MAIN ST # 300,GAITHERSBURG,MD,20878,MARSHALL DR,KNIGHTDALE,NC,27545,APN-WAKE-03,WAKE,ROBERTSON CROSSING LLC,No
LONG GAME LL&C TRUST,,123 MAIN ST,AUSTIN,TX,73301,456 OAK DR,AUSTIN,TX,73301,APN-TRAV-01,TRAVIS,LONG GAME LL&C TRUST,No
LONG GAME LL & C TRUST,,123 MAIN ST,AUSTIN,TX,73301,789 PINE ST,AUSTIN,TX,73301,APN-TRAV-02,TRAVIS,LONG GAME LL & C TRUST,No
```

### 6. Tests

`backend/tests/test_stage_0_cleaning.py` — write a pytest fixture that runs cleaning once, plus these specific tests. ALL must pass:

```python
def test_input_row_count(report):
    assert report.input_rows == 14

def test_final_entity_count(report):
    assert report.unique_entities == 4
    assert len(report.entities) == 4

def test_skip_counts(report):
    assert report.individuals_skipped == 1
    assert report.government_skipped == 1
    assert report.religious_skipped == 1
    assert report.probate_skipped == 1
    assert report.sentinel_skipped == 1
    assert report.data_error_skipped == 1
    assert report.misclassified_individual_skipped == 1
    assert report.total_skipped == 7

def test_robertson_crossing_dedup(report):
    robertson = next(e for e in report.entities if "ROBERTSON CROSSING" in e.entity_name_normalized)
    assert len(robertson.source_parcels) == 3
    apns = {p.apn for p in robertson.source_parcels}
    assert apns == {"APN-WAKE-01", "APN-WAKE-02", "APN-WAKE-03"}

def test_long_game_merge(report):
    long_games = [e for e in report.entities if "LONG GAME" in e.entity_name_normalized]
    assert len(long_games) == 1
    assert len(long_games[0].source_parcels) == 2

def test_treasure_valley_untruncated(report):
    tv = next(e for e in report.entities if "TREASURE VALLEY" in e.entity_name_normalized)
    assert tv.entity_name_cleaned == "TREASURE VALLEY INVESTMENTS LLC"
    assert tv.entity_type.value == "LLC"
    assert "truncated_source_name" in tv.quality_flags

def test_rolator_is_priority(report):
    rolator = next(e for e in report.entities if "ROLATOR" in e.entity_name_normalized)
    assert rolator.is_priority is True
    assert rolator.entity_type.value == "LLC"

def test_all_entities_have_parcels(report):
    for e in report.entities:
        assert len(e.source_parcels) >= 1

def test_long_game_normalization_matches(report):
    # Both "LL&C" and "LL & C" variants must normalize to the same key
    long_game = next(e for e in report.entities if "LONG GAME" in e.entity_name_normalized)
    assert "LL & C" in long_game.entity_name_normalized  # ampersand standardized

def test_missing_required_columns_raises(tmp_path):
    # Verify ValueError when required columns are missing
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("name,address\nFoo LLC,123 Main St")
    import pytest
    from app.pipeline.stage_0_cleaning import clean_csv
    with pytest.raises(ValueError):
        clean_csv(str(bad_csv))
```

### 7. Dockerfile

Base: `mcr.microsoft.com/playwright/python:v1.44.0-jammy`.
Install Python deps via pip from pyproject.toml. Note: base image already has Chromium installed and Python installed. Expose 8000. CMD runs uvicorn.

### 8. Config

```python
# backend/app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Optional now, required in later stages
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    database_url: Optional[str] = None

    google_api_key: Optional[str] = None
    google_search_engine_id: Optional[str] = None
    opencorporates_api_token: Optional[str] = None
    apollo_api_key: Optional[str] = None

settings = Settings()
```

### 9. Logging

structlog. JSON formatter in production, console colored in development. Configured from `settings.log_level`.

### 10. FastAPI minimal app

`/health` returns `{"status": "ok", "version": "0.1.0"}`.

### 11. .gitignore

Must include: `venv/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.env`, `.DS_Store`, `node_modules/`, `dist/`, `build/`, `*.egg-info/`.

### 12. README.md

Quickstart (assuming venv already activated at project root):

```bash
# From project root, with ./venv already activated
cd backend
pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn app.main:app --reload
# visit http://localhost:8000/health
```

## What NOT to do

- DO NOT create a new venv (one exists at ./venv/)
- DO NOT use uv, poetry, pipenv, or conda
- DO NOT implement Stages 1–5
- DO NOT build any frontend code
- DO NOT connect to Supabase
- DO NOT implement scrapers
- DO NOT add CI/CD config
- DO NOT add deps I didn't list

## Acceptance criteria

After you finish:

1. I activate venv and run `cd backend && pip install -e ".[dev]"` — no errors
2. I run `pytest` — all tests pass
3. I run `uvicorn app.main:app --reload` — server starts
4. `curl http://localhost:8000/health` returns `{"status": "ok", "version": "0.1.0"}`

## Before you code

ASK clarifying questions first if ANY rule above is ambiguous or self-inconsistent. Do not invent behavior. After questions are resolved, show me your plan (list of files and what each contains). I will approve before you write code.
