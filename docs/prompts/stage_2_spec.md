I'm building Stage 2 (Entity Parsing) for LeadFinder — a pipeline that takes LLC property ownership records and enriches them with decision-maker contact info. Stage 0 (Data Cleaning) is already built and shipped — 10/10 tests green, committed to git.

This prompt adds Stage 2. Do NOT build other stages. Do NOT add scrapers, HTTP endpoints, SSE streams, or any external integrations. Stage 2 is pure-function transformation of in-memory data.

## Environment context

- Project root: repo with `venv/` at the project root (Python 3.10)
- Activate with `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Unix)
- Backend lives in `backend/`, already has Stage 0 files — DO NOT touch those files unless the spec below explicitly says to
- Existing tests must still pass (`cd backend && python -m pytest -v` = 10/10)

## Project context (read before coding)

LeadFinder is a **single-user local research tool**, not a SaaS. No database,
no cloud storage, no auth. State lives in memory during a run.

See these design docs (located in `docs/design/`) for full context:

- `step_1_system_understanding.md` — scope, edge cases, non-goals
- `step_2_architecture.md` — tech stack (single process, in-memory state)
- `step_3_pipeline_design.md` — pipeline flow
- `step_5_entity_resolution.md` — name matching rules (RELEVANT for this stage)

You don't need to read everything, but if anything in this prompt seems
inconsistent with the design docs, ASK before coding.

## What Stage 2 Does

**Input:** a `CleanedEntity` object (from Stage 0).
**Output:** the same object with two new fields populated:

- `entity_name_search: str` (primary search query form)
- `filing_state_candidates: list[str]` (ordered list of states to try)

Stage 2 also populates one additional internal field for downstream stages:

- `search_name_variants: list[str]` (multiple query variants to try)

This field must be added to the `CleanedEntity` model in `backend/app/models/entity.py`.

## Files to Create

```
backend/app/pipeline/stage_2_parsing.py       NEW
backend/app/pipeline/__init__.py               EXISTS — leave alone
backend/tests/test_stage_2_parsing.py         NEW
backend/tests/fixtures/stage_2_cases.py       NEW (programmatic fixtures, not CSV)
```

## Files to Modify (minimal edits)

```
backend/app/models/entity.py                  MODIFY — add search_name_variants field
```

## Model Change

In `backend/app/models/entity.py`, the `CleanedEntity` class currently has:

```python
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

Add ONE field:

```python
    search_name_variants: list[str] = []
```

Place it immediately after `entity_name_search`. Do not change any other field.

## Stage 2 Logic

### Function signature

```python
# backend/app/pipeline/stage_2_parsing.py

from app.models.entity import CleanedEntity, EntityType

def parse_entity(entity: CleanedEntity) -> CleanedEntity:
    """
    Enriches a cleaned entity with search-ready fields.
    Mutates and returns the same entity object.

    - Sets entity_name_search (primary form for search queries)
    - Sets search_name_variants (alternates to try if primary misses)
    - Sets filing_state_candidates (ordered list of states to query)
    """
    ...
```

### Rule 1: entity_name_search (the canonical search form)

Derived from `entity_name_normalized`. Strip legal boilerplate that confuses
search engines, but keep the core identifiable tokens and the entity suffix.

Transformations (apply in order):

1. **Start from `entity_name_normalized`** (already uppercase, already has
   standardized ampersand spacing and period-normalized suffixes from Stage 0).

2. **Strip trust legal boilerplate:** for entities where `entity_type == TRUST`, remove
   any of these substrings (case-insensitive, with surrounding whitespace):
   - `U/A DTD <anything up to next clause or end>` (e.g., `U/A DTD SEPTEMBER 10, 2018`)
   - `U/A <DATE>`
   - `DTD <DATE>`
   - Bare `U/A` or `DTD` tokens
   - `THE` when it's a trailing token (e.g., `WAYZATA TRUST THE` → `WAYZATA TRUST`)
   - Charitable-remainder clauses: strip anything after `CHARITABLE REMAINDER`
     keeping only the part before

   Use regex with word boundaries. Don't accidentally strip `U/A` from inside
   a real word.

3. **Collapse consecutive whitespace** after stripping.

4. **Handle slash-containing names** (e.g., `STOCKTON/65TH L P`): the primary
   `entity_name_search` keeps the slash. Variants (§ next rule) include slash-replaced
   versions.

5. **Trim.**

Store the result as `entity_name_search`. This is the primary query form.

### Rule 2: search_name_variants (alternates)

A list of 2-5 alternative forms of the entity name for when the primary
doesn't match a SOS portal's search. Order matters (most likely to match first).

Generation rules (include each applicable variant):

1. **Primary** = `entity_name_search` (always first)
2. **AND↔& swap** — if the name contains `&`, add the version with `AND`. If
   it contains `AND` as a standalone word, add the `&` version.
3. **Suffix stripped** — remove the entity suffix (LLC, LP, INC, etc.) as the
   last token. Some SOS portals match the core name better when suffix is
   absent.
4. **Slash variants** — if name contains `/`, add:
   - Slash replaced with space (`STOCKTON 65TH L P`)
   - Slash replaced with `AND` (`STOCKTON AND 65TH L P`)
5. **Trust core** — if entity_type is TRUST and boilerplate was stripped in
   step 1, add the pre-stripping normalized form as a variant (in case some
   portal wants the full legal name).

Dedupe the list. Maximum 5 variants total (primary + up to 4 alternates).
Keep order: primary first, then most-likely alternates.

### Rule 3: filing_state_candidates (where to look)

An ordered list of 2-4 US state codes to query, most likely first.

Generation rules:

1. **Mailing state first** — if `entity.mailing_address.state` is a known US state,
   add it as the first candidate. LLCs most often file in the state where
   they receive mail.

2. **Property state second** — if any of `source_parcels[i].property_state`
   differs from mailing_state, add those next, deduped. A property-owning
   LLC might file in the state where the property is located.

3. **Delaware fallback** — always append `"DE"` last (unless mailing or
   property already is DE). Delaware is the most common LLC filing state
   for out-of-state owners and hedge-fund-type holding structures.

4. **Trust exception:** if `entity_type == TRUST`, return `[]` (empty list).
   Trusts are not SOS-registered entities. Stage 3 will skip SOS lookups for
   trusts per `step_5_entity_resolution.md §5.8`.

Always return states as uppercase 2-letter codes.

## Edge Cases the Tests Must Cover

Write pytest tests covering these cases. The tests create `CleanedEntity`
objects programmatically (not from CSV) and call `parse_entity()` on them.

| #   | Case                                     | Input key fields                                                                 | Expected output                                                                                                                                 |
| --- | ---------------------------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Simple LLC, mailing and property both TX | name=`ROLATOR & INDEPENDENCE LLC`, type=LLC, mailing_state=TX, property_state=TX | search=`ROLATOR & INDEPENDENCE LLC`; variants include `ROLATOR AND INDEPENDENCE LLC` and `ROLATOR & INDEPENDENCE`; filing_states=`["TX", "DE"]` |
| 2   | Multi-state (mailing MD, property NC)    | name=`ROBERTSON CROSSING LLC`, mailing_state=MD, property_state=NC               | filing_states=`["MD", "NC", "DE"]`                                                                                                              |
| 3   | Delaware-domiciled                       | name=`FOO LLC`, mailing_state=DE                                                 | filing_states=`["DE"]` — don't double-add DE                                                                                                    |
| 4   | Trust with legal boilerplate             | name=`FIELDS FAMILY TRUST THE U/A DTD SEPTEMBER 10, 2018`, type=TRUST            | search=`FIELDS FAMILY TRUST`; variants include the boilerplate form; filing_states=`[]`                                                         |
| 5   | Trailing "THE" stripped                  | name=`WAYZATA TRUST THE`, type=TRUST                                             | search=`WAYZATA TRUST`; filing_states=`[]`                                                                                                      |
| 6   | Slash in name                            | name=`STOCKTON/65TH L P`, type=LP, mailing_state=CA                              | variants include `STOCKTON 65TH L P` and `STOCKTON AND 65TH L P`; filing_states=`["CA", "DE"]`                                                  |
| 7   | AND already present (not `&`)            | name=`SMITH AND JONES LLC`                                                       | variants include `SMITH & JONES LLC`                                                                                                            |
| 8   | Charitable remainder                     | name=`COOK INVESTMENTS L P U/A TRUST COOK CHARITABLE REMAINDER`, type=TRUST      | search strips everything after `CHARITABLE REMAINDER`, keeping `COOK INVESTMENTS L P TRUST` (or similar core); filing_states=`[]`               |
| 9   | Suffix stripping in variants             | name=`JEFFERSON OWNSBY LLC`, type=LLC                                            | variants include `JEFFERSON OWNSBY` (no LLC)                                                                                                    |
| 10  | No property state (only mailing)         | mailing_state=TX, no source_parcels                                              | filing_states=`["TX", "DE"]` — don't fail on empty parcels                                                                                      |
| 11  | Dedupe variants                          | name=`ACME LLC`, type=LLC (no & or slash)                                        | variants = `["ACME LLC", "ACME"]` — only 2 entries, no dupes                                                                                    |
| 12  | Maximum 5 variants                       | an entity that could generate 7 variants                                         | list is truncated to 5; primary is always first                                                                                                 |

## Test File Structure

```python
# backend/tests/test_stage_2_parsing.py
import pytest
from uuid import uuid4
from app.models.entity import (
    CleanedEntity, EntityType, MailingAddress, SourceParcel
)
from app.pipeline.stage_2_parsing import parse_entity


def make_entity(
    name: str,
    entity_type: EntityType = EntityType.LLC,
    mailing_state: str = "TX",
    property_states: list[str] = None,
) -> CleanedEntity:
    """Helper to build CleanedEntity for tests."""
    if property_states is None:
        property_states = [mailing_state]

    return CleanedEntity(
        entity_name_raw=name,
        entity_name_cleaned=name,
        entity_name_normalized=name.upper().strip(),
        entity_type=entity_type,
        mailing_address=MailingAddress(
            street="123 MAIN ST",
            city="CITY",
            state=mailing_state,
            zip="00000",
            complete=True,
        ),
        source_parcels=[
            SourceParcel(
                apn=f"APN-{i}",
                property_state=ps,
            )
            for i, ps in enumerate(property_states)
        ],
    )


# Test cases 1-12 from the table above, one test per case
def test_simple_llc_tx():
    e = make_entity("ROLATOR & INDEPENDENCE LLC")
    parse_entity(e)
    assert e.entity_name_search == "ROLATOR & INDEPENDENCE LLC"
    assert "ROLATOR AND INDEPENDENCE LLC" in e.search_name_variants
    assert "ROLATOR & INDEPENDENCE" in e.search_name_variants
    assert e.filing_state_candidates == ["TX", "DE"]

def test_multi_state_md_nc():
    e = make_entity(
        "ROBERTSON CROSSING LLC",
        mailing_state="MD",
        property_states=["NC"],
    )
    parse_entity(e)
    assert e.filing_state_candidates == ["MD", "NC", "DE"]

# ... etc for all 12 cases
```

Write all 12 tests. Each should make an entity, call `parse_entity()`, and
assert specific expectations. Do not over-assert on variant order EXCEPT
that `entity_name_search` is always `search_name_variants[0]`.

## What NOT to Do

- DO NOT touch `stage_0_cleaning.py` or its tests
- DO NOT add any HTTP endpoints
- DO NOT add scrapers or providers
- DO NOT add a run state or orchestrator
- DO NOT add DB code, caching, or quotas
- DO NOT add async code (Stage 2 is synchronous)
- DO NOT add new dependencies beyond what's in pyproject.toml
- DO NOT modify `conftest.py` (Stage 0's fixtures)
- DO NOT add a `skip_summary_text` field or similar

## Acceptance Criteria

After you finish, I should be able to:

1. `cd backend && python -m pytest -v` — ALL tests pass (Stage 0's 10 + Stage 2's 12 = 22)
2. Import `parse_entity` from `app.pipeline.stage_2_parsing` with no errors
3. Instantiate a `CleanedEntity` with the new `search_name_variants` field defaulting to `[]`

## Before You Code

ASK clarifying questions first if ANY rule is ambiguous. Specific questions
you might have (and my preferred answer if you ask):

- "Should I use regex for trust-boilerplate stripping or string operations?"
  → Either is fine; regex is cleaner for the complex patterns
- "Should `entity_name_search` preserve original case or stay uppercase?"
  → Uppercase (matches `entity_name_normalized`); Stage 3 will case-insensitively match anyway
- "What if mailing_state is None?"
  → Use property_state as first candidate. If both are None, return `["DE"]`.
- "What about Canadian provinces or non-US states in mailing_state?"
  → If it's not one of the 50 US states + DC + territories, skip it (treat as unknown); fall through to property_state → DE
- "Should I add a `quality_flag` for entities with no filing_state_candidates (trusts)?"
  → No. The empty list IS the signal. Stage 3 reads it and skips SOS lookup.

Do NOT invent behavior. After questions are resolved, show me your plan
(list of files + one-line description of each change) and wait for my
explicit "approved" before writing code.
