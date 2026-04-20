# Step 5: Entity Resolution

## 5.1 Scope

Entity resolution is the problem of: **given an LLC name from a property
record, find THE legal entity in public records, and extract THE correct
decision-maker.**

It sounds simple. It's not. This document covers:

- Name matching (fuzzy logic + thresholds)
- Multi-candidate disambiguation
- Cross-source reconciliation (same entity, different spellings)
- Officer-to-person matching (LinkedIn, enrichment APIs)
- Ownership chain resolution (LLC owned by LLC, one hop only)
- Known failure modes

Does NOT cover: the actual scrapers (Stage 3 prompt), confidence scoring
math (Step 4), or Python library choices (Step 6 / Stage 3 prompt).

## 5.2 The Four Resolution Problems

### Problem 1: Name Normalization

Our input: `ROLATOR & INDEPENDENCE LLC`
What TX SOS actually stores: `Rolator and Independence, LLC`
What OpenCorporates stores: `ROLATOR AND INDEPENDENCE LLC`
What LinkedIn shows: `Rolator & Independence`

Same entity. Four different strings. We must match them.

### Problem 2: Same-Name Collisions

Searching `ACME LLC` in OpenCorporates returns 47 results across 30 states.
Which one is the owner of the parcel in Frisco, TX? We need address,
jurisdiction, and filing-date signals to disambiguate.

### Problem 3: Officer-to-Person Matching

SOS filing lists `Sivaramaiah Kondru, Managing Member`.
LinkedIn has `Shiva Kondru` (preferred informal name).
Apollo returns `S. Kondru`.

Same person. Three name forms. We must match with ≥ 90% confidence.

### Problem 4: Ownership Chains

`AMH LANDCO BROOKSTONE LLC` is owned by `American Homes 4 Rent` per public
records. The actual decision-maker lives at the parent company, not the SPV.
We resolve one hop only in v1 (deeper hops = Phase 2).

## 5.3 Name Matching: The Algorithm

### Step 1: Normalize Both Sides

Apply Stage 0's normalization rules to BOTH the search name and every
candidate name from sources. Specifically:

1. Uppercase
2. Strip leading/trailing whitespace
3. Collapse interior whitespace runs
4. Standardize ampersand spacing
5. Strip trailing commas
6. Normalize period-separated suffixes (`L.L.C.` → `LLC`)
7. **Additionally for matching:** replace `AND` ↔ `&` (try both)
8. **Additionally for matching:** strip entity suffix for fuzzy comparison

So `Rolator and Independence, LLC` and `ROLATOR & INDEPENDENCE LLC` both
become `ROLATOR & INDEPENDENCE LLC` for exact-match attempt, and
`ROLATOR INDEPENDENCE` for fuzzy-match attempt.

### Step 2: Exact Match First

If normalized search name equals normalized candidate name → **exact match,
score = 1.0**. Done.

### Step 3: Fuzzy Match via Token Sort Ratio

Use `rapidfuzz.fuzz.token_sort_ratio` — handles word reordering robustly.

```python
from rapidfuzz import fuzz

def name_similarity(search: str, candidate: str) -> float:
    """Returns 0.0 to 1.0."""
    search_norm = normalize_for_matching(search)
    candidate_norm = normalize_for_matching(candidate)

    if search_norm == candidate_norm:
        return 1.0

    # token_sort_ratio handles "A B LLC" matching "B A LLC"
    return fuzz.token_sort_ratio(search_norm, candidate_norm) / 100.0
```

### Step 4: Thresholds

| Similarity  | Classification | Action                                    |
| ----------- | -------------- | ----------------------------------------- |
| ≥ 0.95      | Strong match   | Accept                                    |
| 0.85 – 0.94 | Probable match | Accept, flag `fuzzy_match_uncertain`      |
| 0.70 – 0.84 | Weak match     | Return as candidate; needs disambiguation |
| < 0.70      | Rejection      | Not the same entity                       |

### Why These Numbers

- `0.95+`: catches typos, spacing differences, `AND`↔`&`, suffix variants. Safe.
- `0.85 – 0.94`: catches reorderings (`SMITH JONES LLC` ↔ `JONES SMITH LLC`) and minor word additions. Mostly correct but can confuse "GARDEN LLC" with "GARDEN VIEW LLC"; flag so reviewer sees it.
- `0.70 – 0.84`: nets candidates that need address/state evidence to confirm. Not an auto-match.
- `< 0.70`: different entity.

These thresholds are starting points. Revisit after Stage 3 is built and we
see how they perform on real data.

### Name Matching Never Fires Alone

A 0.95 name match alone doesn't mean "same entity" — could be same name in
different states. Always combine with:

- Filing state (if known)
- Mailing address (if available)
- Officers list (same people across sources = strong signal)

The name match is a _candidate generator_. Final identity decision uses
multiple signals.

## 5.4 Multi-Candidate Disambiguation

When we search `SMITH HOLDINGS LLC` in OpenCorporates and get 14 results,
we rank them.

### Ranking signals (in priority order)

**1. Jurisdiction match**

If our input entity has a mailing state (say, TX), candidates filed in TX
score highest. Candidates in DE score next (common LLC jurisdiction for
out-of-state owners). All others score lowest.

```python
def jurisdiction_score(candidate_state: str, mailing_state: str) -> float:
    if candidate_state == mailing_state:
        return 1.0
    if candidate_state == "DE":
        return 0.6  # Common LLC-filing state for out-of-state owners
    return 0.2
```

**2. Mailing address match**

If the SOS-filed principal office OR registered agent address is fuzzy-
close to our input's mailing address (same city, same street within 80%
similarity), that's a strong confirmation.

```python
def address_score(candidate_addr: dict, our_addr: dict) -> float:
    if not candidate_addr or not our_addr:
        return 0.0
    if candidate_addr.get("zip") == our_addr.get("zip"):
        city_match = fuzz.ratio(
            candidate_addr.get("city", "").upper(),
            our_addr.get("city", "").upper(),
        ) / 100.0
        if city_match >= 0.90:
            return 0.9
    # Softer match: same city, same state
    if (candidate_addr.get("city", "").upper() == our_addr.get("city", "").upper()
        and candidate_addr.get("state") == our_addr.get("state")):
        return 0.6
    return 0.0
```

**3. Entity status**

Active > Inactive > Dissolved > Withdrawn. An active filing is more likely
to be "the one" than an old dissolved one.

**4. Filing date recency**

More recent filings are more likely to be relevant for a property acquired
recently. Use filing date if we have it; otherwise skip.

### Composite disambiguation score

```python
def disambiguation_score(candidate, our_entity) -> float:
    name_sim = name_similarity(our_entity.name, candidate.name)
    juris = jurisdiction_score(candidate.state, our_entity.mailing_state)
    addr = address_score(candidate.principal_office, our_entity.mailing_address)
    status_bonus = {"active": 0.1, "inactive": 0.0, "dissolved": -0.1}.get(
        candidate.status, 0.0
    )

    return (
        name_sim * 0.40
        + juris * 0.25
        + addr * 0.30
        + status_bonus * 0.05
    )
```

Sort all candidates by this score. **If the top score is ≥ 0.15 above the
second-highest**, accept the top. Otherwise flag `ambiguous_match`, return
the top 3 candidates, let the reviewer pick via audit JSON.

## 5.5 Officer-to-Person Matching

SOS filings return officer names like `Sivaramaiah Kondru`. We then search
LinkedIn or B2B enrichment APIs. They return variants:

- `Shiva Kondru` (LinkedIn preferred name)
- `S. Kondru` (abbreviated)
- `Sivaramaiah "Shiva" Kondru` (formal with nickname)
- `KONDRU SIVARAMAIAH` (last-first format)

### Name match algorithm

```python
def officer_match(sos_name: str, found_name: str) -> float:
    """Returns 0.0 to 1.0 confidence that these refer to the same person."""
    sos_parts = tokenize_person_name(sos_name)
    found_parts = tokenize_person_name(found_name)

    # Last name must match (allow fuzzy for transliterations)
    last_sim = fuzz.ratio(sos_parts.last, found_parts.last) / 100.0
    if last_sim < 0.85:
        return 0.0  # Different last names = different people

    # First name: handle nickname-to-formal (Shiva ↔ Sivaramaiah)
    first_sim = best_first_name_similarity(sos_parts.first, found_parts.first)

    # Middle initial (if both have): small bonus, no penalty for absence
    middle_bonus = 0.05 if sos_parts.middle == found_parts.middle else 0.0

    return min(1.0, 0.4 * last_sim + 0.5 * first_sim + middle_bonus)
```

### Nickname-to-formal matching

Common nicknames: `Shiva` for `Sivaramaiah`, `Bob` for `Robert`, `Bill` for
`William`, `Jim` for `James`, etc. Use the
[`nicknames` Python package](https://pypi.org/project/nicknames/) which has
a prebuilt lookup table.

```python
def best_first_name_similarity(a: str, b: str) -> float:
    direct = fuzz.ratio(a, b) / 100.0
    if direct >= 0.90:
        return direct

    # Check nickname relationship
    if nicknames.are_nicknames(a, b):
        return 0.90

    # Substring match (e.g., "Shiva" in "Sivaramaiah 'Shiva' Kondru")
    if a.lower() in b.lower() or b.lower() in a.lower():
        return 0.85

    return direct
```

### Threshold

Officer-to-person match ≥ 0.90 = confident same person.
Match 0.75 – 0.89 = probable, flag `officer_match_uncertain`.
Match < 0.75 = different person; try next candidate or skip.

## 5.6 Cross-Source Reconciliation

When we find the same entity in multiple sources, we merge the data —
but **we keep source attribution** so scoring knows where each value came from.

Example: FL Sunbiz + LinkedIn both return data on
`ROLATOR & INDEPENDENCE LLC`.

```python
entity.sos_results = [
    {
        "source": "fl_direct",
        "filing_number": "0804278580",
        "status": "Active",
        "principal_office": {...},
        "officers": [
            {"name": "Sivaramaiah Kondru", "title": "Managing Member"},
        ],
    },
]

entity.contacts = [
    {
        "source": "serper_linkedin",
        "linkedin_url": "linkedin.com/in/shiva-kondru-17447bab",
        "name_from_profile": "Shiva Kondru",
        "title_from_profile": "Managing Member at Rolator & Independence LLC",
    },
]
```

### Merging rules

- **Officers:** dedupe by `officer_match(name_a, name_b) >= 0.90`. If same
  person appears in 2 sources, keep both source tags but treat as one person.
- **Addresses:** keep all unique; prefer SOS-filed over aggregator-reported
  when scoring.
- **Titles:** if one source says "Managing Member" and another says
  "Manager", prefer the more specific (Managing Member > Manager >
  Officer > Registered Agent).

No destructive merging. Every source's raw response is preserved in the
audit JSON.

## 5.7 Ownership Chain Resolution (One Hop)

Some LLCs are owned by other LLCs. Example:

```
AMH LANDCO BROOKSTONE LLC
  ↳ owned by: AMERICAN HOMES 4 RENT LLC
              (which is part of the American Homes 4 Rent REIT)
```

The decision-maker isn't at the SPV; it's at the parent.

### Detection

When we pull officers from `AMH LANDCO BROOKSTONE LLC` and see:

- Manager: `AMERICAN HOMES 4 RENT, LLC`
- Registered Agent: some corporate law firm

That's a clear signal the decision-maker lives at the parent.

### Resolution (v1: one hop)

```python
def resolve_ownership_chain(entity, depth=0, max_depth=1):
    if depth >= max_depth:
        return entity

    officers = entity.sos_results[0].officers
    entity_like_officer = next(
        (o for o in officers if looks_like_entity(o.name)), None
    )
    if not entity_like_officer:
        return entity  # Officers are real people; we're done

    # Recurse: search for the parent entity
    parent_name = entity_like_officer.name
    parent_entity = await search_sos(parent_name, ...)
    if parent_entity and parent_entity.sos_results:
        entity.parent_company = parent_name
        entity.parent_officers = parent_entity.sos_results[0].officers
        # The real decision-maker is at the parent
        return parent_entity

    return entity
```

`looks_like_entity()` returns True if the "officer" name contains `LLC`,
`INC`, `LP`, `TRUST`, etc. — i.e., it's a company, not a person.

### Why one hop only

Deeper chains (`A owns B owns C owns D`) get ambiguous quickly. Holding
companies and trusts can obscure the actual decision-maker for real
investigative reasons. v1 does one hop; v2 can add deeper traversal with
cycle detection.

**Quality flag:** add `ownership_chain_resolved` when we hop. The reviewer
knows the row's decision-maker isn't directly from the LLC on the property
but from one level up.

## 5.8 Trusts Are Different

SOS registries don't usually have trust data. A `JOHNSON REVOCABLE LIVING
TRUST` won't show up in FL Sunbiz or NC SOS.

### How we handle trusts

1. Stage 2 detects `entity_type == TRUST` from Stage 0's cleaned data.
2. Stage 3 skips SOS lookup entirely for TRUSTs. Returns `sos_results: []`.
3. Stage 4 uses the trust name as a search target directly — Serper search
   for `"Johnson Revocable Living Trust" site:linkedin.com` or similar.
4. Without a decision-maker from SOS, we rely entirely on:
   - County assessor records (not implemented in v1)
   - Google search for news mentions, trustee names
   - Mailing address WHOIS / reverse address lookups
5. Confidence tier is almost always MEDIUM or LOW for trusts on free tier.

**Quality flag:** trusts already have `trust_with_legal_boilerplate` in
some cases. Add `trust_no_sos_data` when Stage 3 skips.

## 5.9 Known Failure Modes

**F1 — Same entity, different LLCs in different states with same name.**
`JOHNSON PROPERTIES LLC` exists as active filings in 19 states. Without
mailing-address match, we can't disambiguate. Action: flag
`ambiguous_match`, return top 3, confidence LOW.

**F2 — Registered Agent is a corporate service company.**
`CT CORPORATION SYSTEM`, `LEGALZOOM.COM INC`, `CAPITOL SERVICES`. The RA
is not the decision-maker. Maintain a denylist of known RA service
companies; always prefer officers over RA when both exist. If only RA is
available, flag `only_registered_agent`, confidence drops.

**F3 — CA/NY don't publish members.**
Statement of Information in CA does list officers. NY publishes only
registered agent. We document the limitation; confidence for CA reflects
one-source reliance; confidence for NY is typically LOW.

**F4 — Officer name is a P.O. Box or incomplete string.**
Sometimes SOS filings have garbage in officer name fields. Detect with:

- Contains digits or P.O. box format → not a person name
- Length < 3 or > 80 chars → likely garbage
- All caps with only one word → likely incomplete

Flag these; don't use as decision-maker; fall back to next candidate.

**F5 — LinkedIn URL doesn't load or returns a different profile.**
Serper returns URLs, but we don't load them for verification (scope). If a
later stage does load and finds a name mismatch, flag
`linkedin_url_mismatch`, drop the LinkedIn field from the row.

**F6 — Multi-member LLC with no obvious "lead" member.**
`STAR 340 LLC` has 2 members: `BHPT ENTERPRISES` + `PERRY PARDOE`. The
reference output picked `PERRY PARDOE` because the other is an entity (not
a person). Rule: prefer person over entity. If all members are people,
prefer by title (Manager > Member), else first in list, flag
`multi_member_no_lead`.

## 5.10 What Step 5 Delivers to Stage 3/4/5 Builds

Inputs that the stage prompts will reference:

**For Stage 3 (SOS Lookup):**

- Name normalization rules
- Multi-candidate disambiguation logic
- Ownership chain detection (one hop)
- Trust handling (skip SOS)

**For Stage 4 (Contact Enrichment):**

- Officer-to-person matching with nicknames
- LinkedIn URL acceptance thresholds
- RA service company denylist

**For Stage 5 (Output):**

- Cross-source reconciliation rules
- Multi-member "pick the lead" rule
- Quality flag surfacing

Step 5's job is to define the _algorithms_. Stages 3/4/5 implement them in
code. We revisit these thresholds after running the full `main_data.csv`
and checking against the expected-output file.

## 5.11 What Step 5 Does NOT Cover

- Confidence tier mapping → Step 4
- Specific scraper implementations → Stage 3 prompt
- Specific enrichment provider implementations → Stage 4 prompt
- SQL / DB schema — we don't have one
- Scoring formula — Step 4
