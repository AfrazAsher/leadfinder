I'm building Stage 5 (Output + Confidence Scoring) for LeadFinder. Stages 0, 2, 1, and 3a are shipped (52 tests passing). Stage 3a extracts real officer data from FL Sunbiz into `entity.sos_results[0]["officers"]`, but the output CSV still shows "Not Found" for Decision Maker because the current Stage 5 is a placeholder. This prompt builds real Stage 5.

## What Stage 5 Does

Given an entity with populated `sos_results`, Stage 5:

1. Selects the best officer by title priority
2. Computes a confidence score per step_4_confidence_scoring.md
3. Assigns a tier: HIGH / MEDIUM-HIGH / MEDIUM / LOW / blank
4. Populates `entity.final_decision_maker`, `final_title`, `final_address`, `final_confidence`
5. Writes output CSV (1 row per entity, winning officer only)
6. Writes audit JSON alongside the CSV (all candidates, scores, tier)

## Environment Context

- Project root: C:\Users\Afraz\Desktop\Ussama\Office-work\leadfinder
- Python 3.10, venv at ./venv/
- Previous stages passing: 52 tests. Don't break any.
- All design docs in docs/design/. Before coding, read step_4_confidence_scoring.md carefully — it's the formula.

## Files to Touch

### NEW files

- `backend/app/pipeline/scoring.py` — confidence scoring + tier mapping
- `backend/app/pipeline/officer_selection.py` — title-priority picker
- `backend/tests/test_scoring.py` — unit tests for scoring
- `backend/tests/test_officer_selection.py` — unit tests for officer picker
- `backend/tests/test_stage_5_output.py` — integration tests for real Stage 5

### REPLACED

- `backend/app/pipeline/stage_5_output.py` — real implementation (current is placeholder)

### DO NOT TOUCH

- Stage 0 code, Stage 1 code, Stage 2 code, Stage 3a code (sos_fl, sos_nc, stage_3_sos)
- orchestrator.py (already calls stage_5)
- Models, routers, main.py, run.py
- Any existing test file

## Models Already Available

`backend/app/models/entity.py` CleanedEntity already has these runtime fields from Stage 1:

- `status: str` — "pending" / "in_progress" / "resolved" / "unenriched" / "failed"
- `sos_results: list[dict]` — list of SOSResult.to_dict() outputs
- `sos_source: Optional[str]` — e.g., "fl_direct"
- `contacts: list[dict]` — from Stage 4 (empty in v1)
- `final_decision_maker: Optional[str]`
- `final_title: Optional[str]`
- `final_address: Optional[dict]`
- `final_linkedin: Optional[str]`
- `final_email: Optional[str]`
- `final_phone: Optional[str]`
- `final_confidence: Optional[float]`
- `final_tier: Optional[str]`
- `error_message: Optional[str]`

All these are already wired. Stage 5 just populates them.

## Detailed Specs

### 1. Officer Selection (`officer_selection.py`)

Title priority (highest → lowest):

```
TITLE_PRIORITY = [
    # Member-managed LLC: the SOLE MEMBER is the owner
    ("sole member", 100),
    ("sole mgr", 100),
    ("member/manager", 95),
    ("managing member", 95),

    # Manager-managed LLC
    ("mgrm", 90),
    ("manager", 90),
    ("mgr", 90),
    ("ambr", 85),
    ("authorized member", 85),
    ("member", 80),

    # Corporation
    ("ceo", 75),
    ("president", 70),
    ("pres", 70),
    ("chairman", 65),
    ("chair", 65),

    # LP / LLP
    ("general partner", 88),
    ("gen ptr", 88),
    ("gp", 88),

    # Officers
    ("cfo", 55),
    ("coo", 55),
    ("vp", 50),
    ("vice president", 50),
    ("treasurer", 45),
    ("secretary", 40),
    ("sec", 40),

    # Admin
    ("assistant secretary", 20),
    ("asst sec", 20),
    ("trustee", 35),
    ("director", 30),
]
```

Algorithm:

```python
def select_best_officer(officers: list[dict]) -> Optional[dict]:
    """
    Return the officer with the highest-priority title.
    Title matching: lowercase, strip punctuation, check if any
    priority phrase appears as a substring. First match wins.

    Tie-breaker: if multiple officers share the top priority,
    return the first in document order (Sunbiz's order ≈ seniority).

    Returns None if officers is empty.
    """
```

Matching details:

- Normalize title: lowercase, replace `&` with `and`, strip trailing punctuation, collapse whitespace
- Iterate priority list in declared order, for each (phrase, score) check if phrase appears as substring of normalized title
- Take the highest score found across all officers; if tied, first-in-document-order wins
- If NO officer has any priority match (e.g., all titles are exotic), default to the first officer in document order with priority score 10

### 2. Confidence Scoring (`scoring.py`)

Per `docs/design/step_4_confidence_scoring.md`:

**Per-field source reliability:**

```python
SOURCE_RELIABILITY = {
    "fl_direct": 0.90,
    "nc_direct": 0.90,
    "wa_direct": 0.90,  # future
    "ut_direct": 0.90,  # future
    "serper_linkedin": 0.60,   # Stage 4
    "hunter_email": 0.70,       # Stage 4
    "apollo_phone": 0.75,       # Stage 4
}
```

**Status adjustment:**

```python
STATUS_MULTIPLIER = {
    "active": 1.0,
    "current-active": 1.0,
    "current": 1.0,
    "inactive": 0.5,
    "dissolved": 0.3,
    "withdrawn": 0.3,
    "admin dissolved": 0.3,
}
```

**Entity identity score (for the winning officer):**

```python
def entity_identity_score(entity, winning_officer) -> float:
    """
    Combine:
    - source reliability (max 1.0)
    - SOS status multiplier (reward only Active entities)
    - officer title priority normalized (max 0.15)
    - data completeness (has filing_number, has address, has officer address)

    Base formula:
        score = source_rel * status_mult * (0.7 + 0.15 * title_norm + 0.15 * completeness)

    Where:
        title_norm = title_priority_score / 100.0  (clamp to 0..1)
        completeness = 0 to 1 based on fields populated:
            filing_number present: +0.4
            entity principal_address present: +0.3
            winning_officer address present: +0.3
    """
```

**Tier mapping:**

```python
def tier_for_score(score: float) -> str:
    if score >= 0.85:
        return "HIGH"
    elif score >= 0.70:
        return "MEDIUM-HIGH"
    elif score >= 0.55:
        return "MEDIUM"
    elif score >= 0.40:
        return "LOW"
    else:
        return "blank"
```

### 3. Stage 5 Output (`stage_5_output.py`)

Complete rewrite. Signature stays the same:

```python
async def write_output(state: AppState, entities: list[CleanedEntity]) -> Path:
    """
    1. For each resolved entity, select best officer + compute confidence
    2. Populate entity.final_* fields
    3. Write CSV to temp file (10-column schema unchanged)
    4. Write audit JSON alongside the CSV
    5. Return CSV path
    """
```

Processing loop:

```python
for entity in entities:
    if entity.status != "resolved" or not entity.sos_results:
        # Unenriched or failed; leave final_* as None
        continue

    sos = entity.sos_results[0]
    officers = sos.get("officers", [])

    if not officers:
        # SOS found the entity but no officers listed (rare)
        entity.final_decision_maker = None
        entity.final_confidence = 0.0
        entity.final_tier = "blank"
        continue

    best = select_best_officer(officers)
    if best is None:
        continue

    entity.final_decision_maker = best["name"]
    entity.final_title = best.get("title")
    entity.final_address = best.get("address") or sos.get("principal_address")

    score = entity_identity_score(entity, best)
    entity.final_confidence = round(score, 3)
    entity.final_tier = tier_for_score(score)
```

### CSV Schema (unchanged from Stage 1)

```
#, LLC Company, Decision Maker Name, Parent/Company Name, Website, Job Title, LinkedIn, Email, Phone Number, Confidence
```

Population rules:

- `#`: 1-based row index
- `LLC Company`: `entity.entity_name_cleaned`
- `Decision Maker Name`: `entity.final_decision_maker` or "Not Found" if blank tier or None
- `Parent/Company Name`: blank for v1 (ownership chain = Stage 3b)
- `Website`: blank for v1 (Stage 4)
- `Job Title`: `entity.final_title` or blank
- `LinkedIn`: blank for v1
- `Email`: blank for v1
- `Phone Number`: blank for v1
- `Confidence`: `entity.final_tier` or "blank"

When tier is "blank", the Decision Maker column reads "Not Found" (preserve current UX).

### Audit JSON Schema

Write to same directory as CSV, filename same root with `.audit.json` suffix.

```json
{
  "run_id": "xxxxx",
  "generated_at": "2026-04-20T15:30:00Z",
  "total_entities": 3,
  "entities": [
    {
      "entity_name_cleaned": "DISNEY DESTINATIONS LLC",
      "status": "resolved",
      "sos_source": "fl_direct",
      "sos_result": {
        "filing_number": "L99000007022",
        "entity_name": "DISNEY DESTINATIONS, LLC",
        "status": "ACTIVE",
        "principal_address": {...},
        "mailing_address": {...},
        "registered_agent": {...},
        "officers": [...all 10...]
      },
      "selection": {
        "chosen_index": 5,
        "chosen_officer": {
          "name": "Walt Disney Attractions Trust",
          "title": "Sole Member",
          "address": {...}
        },
        "title_priority_score": 100,
        "reasoning": "Highest priority match: 'sole member' in title"
      },
      "confidence": {
        "final_score": 0.855,
        "tier": "HIGH",
        "components": {
          "source_reliability": 0.90,
          "status_multiplier": 1.0,
          "title_normalized": 1.0,
          "completeness": 1.0
        }
      }
    }
  ]
}
```

Also emit a structured `done` event with `output_path`, `audit_path` so the router can serve both.

### 4. Router update

`backend/app/routers/download.py` currently serves one file. Add optional audit download:

- `GET /api/download?format=csv` (default) → returns CSV
- `GET /api/download?format=audit` → returns audit JSON

Both require a completed run; both return 404 if no run or no file yet.

## Tests

### `test_officer_selection.py` (~8 tests)

```python
def test_sole_member_wins_over_assistant_secretary():
    officers = [
        {"name": "Asst Sec", "title": "Assistant Secretary"},
        {"name": "The Owner", "title": "Sole Member"},
    ]
    result = select_best_officer(officers)
    assert result["name"] == "The Owner"

def test_manager_wins_over_member():
    # Manager > Member in priority
    officers = [
        {"name": "Joe Member", "title": "Member"},
        {"name": "Jane Manager", "title": "Manager"},
    ]
    assert select_best_officer(officers)["name"] == "Jane Manager"

def test_mgr_abbreviation_matched():
    officers = [{"name": "Jane", "title": "MGR"}]
    assert select_best_officer(officers)["name"] == "Jane"

def test_ties_broken_by_document_order():
    officers = [
        {"name": "First MGR", "title": "Manager"},
        {"name": "Second MGR", "title": "Manager"},
    ]
    assert select_best_officer(officers)["name"] == "First MGR"

def test_exotic_title_defaults_to_first():
    officers = [
        {"name": "First Exotic", "title": "Chief Revenue Wizard"},
        {"name": "Second Exotic", "title": "Head of Vibes"},
    ]
    assert select_best_officer(officers)["name"] == "First Exotic"

def test_empty_officers_returns_none():
    assert select_best_officer([]) is None

def test_case_insensitive_matching():
    officers = [{"name": "J", "title": "manager"}]
    assert select_best_officer(officers)["name"] == "J"

def test_general_partner_beats_secretary():
    officers = [
        {"name": "Sec", "title": "Secretary"},
        {"name": "GP", "title": "General Partner"},
    ]
    assert select_best_officer(officers)["name"] == "GP"
```

### `test_scoring.py` (~7 tests)

```python
def test_tier_high_boundary():
    assert tier_for_score(0.85) == "HIGH"
    assert tier_for_score(0.849999) == "MEDIUM-HIGH"

def test_tier_all_boundaries():
    assert tier_for_score(0.70) == "MEDIUM-HIGH"
    assert tier_for_score(0.55) == "MEDIUM"
    assert tier_for_score(0.40) == "LOW"
    assert tier_for_score(0.399) == "blank"

def test_active_fl_with_manager_scores_high():
    entity = _make_entity_with_sos("fl_direct", "Active", filing_number="L123", principal_present=True)
    officer = {"name": "X", "title": "Manager", "address": {"street": "1 Main"}}
    score = entity_identity_score(entity, officer)
    assert score >= 0.85

def test_dissolved_entity_scores_low():
    entity = _make_entity_with_sos("fl_direct", "Dissolved", filing_number="L123", principal_present=True)
    officer = {"name": "X", "title": "Manager", "address": {"street": "1 Main"}}
    score = entity_identity_score(entity, officer)
    assert score < 0.5  # Dissolved multiplier kills it

def test_missing_address_reduces_completeness():
    entity = _make_entity_with_sos("fl_direct", "Active", filing_number="L123", principal_present=False)
    officer = {"name": "X", "title": "Manager", "address": None}
    full_entity = _make_entity_with_sos("fl_direct", "Active", filing_number="L123", principal_present=True)
    full_officer = {"name": "X", "title": "Manager", "address": {"street": "1 Main"}}
    assert entity_identity_score(entity, officer) < entity_identity_score(full_entity, full_officer)

def test_exotic_title_reduces_score():
    entity = _make_entity_with_sos("fl_direct", "Active", filing_number="L123", principal_present=True)
    with_manager = {"name": "X", "title": "Manager", "address": {"street": "1"}}
    with_exotic = {"name": "X", "title": "Chief Revenue Wizard", "address": {"street": "1"}}
    assert entity_identity_score(entity, with_manager) > entity_identity_score(entity, with_exotic)

def test_score_clamped_to_01_range():
    # Sanity: score should never exceed 1.0 or drop below 0
    entity = _make_entity_with_sos("fl_direct", "Active", filing_number="L123", principal_present=True)
    officer = {"name": "X", "title": "Sole Member", "address": {"street": "1"}}
    s = entity_identity_score(entity, officer)
    assert 0.0 <= s <= 1.0
```

(Helper `_make_entity_with_sos` builds a minimal CleanedEntity with `sos_results` + `sos_source` set.)

### `test_stage_5_output.py` (~5 tests)

Minimal orchestrated-end-to-end tests:

```python
@pytest.mark.asyncio
async def test_stage_5_writes_real_decision_maker():
    """Entity with Disney-like officer list → 'Sole Member' wins."""
    state = _make_state_with_run()
    entity = _make_resolved_entity_with_officers([
        {"name": "Asst Sec A", "title": "Assistant Secretary", "address": {...}},
        {"name": "Walt Disney Attractions Trust", "title": "Sole Member", "address": {...}},
    ])
    path = await write_output(state, [entity])
    content = path.read_text()
    assert "Walt Disney Attractions Trust" in content
    assert "Sole Member" in content
    assert "HIGH" in content or "MEDIUM-HIGH" in content


@pytest.mark.asyncio
async def test_stage_5_writes_not_found_for_unenriched():
    state = _make_state_with_run()
    entity = _make_unenriched_entity()
    path = await write_output(state, [entity])
    content = path.read_text()
    assert "Not Found" in content


@pytest.mark.asyncio
async def test_stage_5_writes_audit_json_alongside_csv():
    state = _make_state_with_run()
    entity = _make_resolved_entity_with_officers([
        {"name": "Jane", "title": "Manager", "address": {"street": "1 Main"}}
    ])
    csv_path = await write_output(state, [entity])
    audit_path = csv_path.with_suffix(".audit.json")
    assert audit_path.exists()
    data = json.loads(audit_path.read_text())
    assert data["total_entities"] == 1
    assert data["entities"][0]["selection"]["chosen_officer"]["name"] == "Jane"


@pytest.mark.asyncio
async def test_stage_5_dissolved_entity_tier_reflects_status():
    state = _make_state_with_run()
    entity = _make_resolved_entity_with_officers(
        [{"name": "X", "title": "Manager"}],
        status="Dissolved",
    )
    await write_output(state, [entity])
    assert entity.final_tier in ("LOW", "blank")


@pytest.mark.asyncio
async def test_stage_5_csv_has_10_columns():
    state = _make_state_with_run()
    entity = _make_resolved_entity_with_officers([{"name": "Jane", "title": "Manager"}])
    path = await write_output(state, [entity])
    import csv
    rows = list(csv.reader(path.open()))
    assert len(rows[0]) == 10  # header
    assert len(rows[1]) == 10  # data row
```

## Acceptance Criteria

1. `pytest -v` → 52 existing + ~20 new = **~72 tests passing**
2. No existing test breaks
3. Manual smoke test: upload Disney/Universal/Walmart CSV; output CSV shows a real name in Decision Maker column, with Title populated, and Confidence = HIGH or MEDIUM-HIGH.
4. Audit JSON exists alongside the CSV and contains full candidate details.
5. `uvicorn run.py` still starts cleanly

## What NOT to Do

- DO NOT add Stage 4 contact enrichment (LinkedIn/Email/Phone) — leave as blank
- DO NOT modify Stage 0/1/2/3a code
- DO NOT modify the CleanedEntity model schema
- DO NOT change the CSV column schema
- DO NOT add new routes or break existing ones
- DO NOT add a database (we're single-user in-memory)

## Before You Code

Ask clarifying questions if anything's ambiguous. Likely questions:

- **"What if sos_results has multiple entries?"** → Take sos_results[0]. Stage 3a writes exactly one result per entity (the winning state). Stage 3b won't change this.

- **"What about contacts from Stage 4?"** → Ignore in v1. Contact columns stay blank. In v2 we'll add `select_best_contact` similar to `select_best_officer`.

- **"Should the winning officer's address OVERRIDE the entity's principal address in the CSV?"** → Not explicitly in v1 CSV. The CSV has one "Decision Maker Address" implicitly via the Confidence column reasoning, but the column set doesn't include it. Only `final_address` in the entity dict is populated (for future use). The officer's address is in the audit JSON.

- **"Does the audit JSON replace or add to what's already written?"** → Adds. Stage 5 currently writes only a CSV. Now it writes CSV + audit JSON.

- **"What if tier is 'blank' — do we drop the row or write 'Not Found'?"** → Keep the row, write "Not Found" in Decision Maker column, "blank" in Confidence column. Preserve current UX for LLC Company column.

Show me your plan (files + 1-line description each) and any clarifying questions. Wait for "approved" before writing code.
