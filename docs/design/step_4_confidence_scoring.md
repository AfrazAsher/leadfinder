# Step 4: Confidence Scoring

## 4.1 Scope

Defines the formula that turns raw enrichment data into a confidence tier
for each row in the output CSV. Covers:

- What a confidence score means (and doesn't mean)
- Per-field scoring (name, phone, email, LinkedIn, mailing address)
- Entity-level tier calculation (HIGH / MEDIUM-HIGH / MEDIUM / LOW)
- Source independence weighting
- Tie-breaking and selection logic
- How the scope requirement ("up to 5 phones, 5 emails") fits

Does NOT cover: how to find the data (Step 3 for SOS, Stage 4 prompt for
enrichment), how to fuzzy-match entity names across sources (Step 5).

## 4.2 What "Confidence" Means

Confidence is our answer to: **"If the reviewer acts on this row — calls the
phone, emails the address, sends outreach — how likely is it that they reach
the right person?"**

It is NOT:

- Data freshness
- Source reputation in isolation
- Whether we found data (that's the `status` field: resolved/unenriched)
- Completeness (partial rows can still be HIGH-confidence on what they have)

It's a **decision signal for the reviewer.** HIGH means "trust and use
directly." LOW means "use only with manual verification."

## 4.3 Tier Definitions

Four tiers, matching the expected output file:

| Tier            | Meaning                                                               | Reviewer action           |
| --------------- | --------------------------------------------------------------------- | ------------------------- |
| **HIGH**        | 2+ independent sources agree on core identity AND contact             | Use directly              |
| **MEDIUM-HIGH** | Core identity confirmed by 2+ sources; contact from 1 reliable source | Use with light spot-check |
| **MEDIUM**      | Single authoritative source (e.g., SOS filing); contact unverified    | Verify before outreach    |
| **LOW**         | Weak signals; name match uncertain OR only inferred data              | Manual research needed    |

Entities with zero data produce output rows with `"Not Found"` values and a
**blank** confidence field (not a LOW — they're `unenriched`, which is
different from "we tried and got weak signals").

## 4.4 Three-Layer Scoring Model

The score isn't one number. It's three layers that compose.

```
Layer 1: Per-field scores (0.0 to 1.0)
    decision_maker_name_score
    phone_score (for each of up to 5 phones)
    email_score (for each of up to 5 emails)
    linkedin_score
    mailing_address_score

Layer 2: Aggregate to entity identity score (0.0 to 1.0)
    How confident are we that "person X is the decision-maker for LLC Y"?

Layer 3: Map to tier (HIGH / MEDIUM-HIGH / MEDIUM / LOW)
    Combines entity identity score + data availability + independence count
```

Each layer is computed in Stage 5. Inputs come from Stages 3 and 4.

## 4.5 Layer 1: Per-Field Scoring

Every piece of extracted data carries a score based on:

- **Source reliability** (how trustworthy is the underlying source?)
- **Validation outcome** (did external validation pass?)
- **Context fit** (does it match the entity we're researching?)

### Source reliability baseline

| Source                            | Baseline reliability | Why                                     |
| --------------------------------- | -------------------- | --------------------------------------- |
| Direct SOS filing (FL/NC/WA/UT)   | 0.95                 | Official record, government-issued      |
| TX SOSDirect (paid, stubbed v1)   | 0.95                 | Same, different state                   |
| OpenCorporates                    | 0.75                 | Aggregator of SOS, possibly stale       |
| CorporationWiki / Bizapedia       | 0.55                 | Derivative, stale, scraped              |
| Company website (About/Team page) | 0.70                 | Usually truthful but self-reported      |
| LinkedIn (via Serper search)      | 0.80                 | Actively maintained by person           |
| ZoomInfo / Apollo                 | 0.80                 | Professional enrichment, often accurate |
| Hunter (email pattern guess)      | 0.50                 | Generated, not observed                 |
| Apollo email                      | 0.70                 | Verified inbox often                    |

These are **starting points**. Validation and context adjust them.

### Validation adjustments

**Phone:**

- Library-validated with `phonenumbers` (valid format + country) → +0.05
- Line-type check: mobile/landline/VOIP known → +0.05
- Numverify/similar live check: reachable → +0.10

**Email:**

- Syntax valid (`email-validator`) → +0.03
- MX record exists (dnspython) → +0.05
- Full deliverability check (SMTP/Hunter verify) → +0.10
- Generic/role-based (info@, contact@) → -0.15 (lower, but not zero)

**LinkedIn:**

- URL resolves (HEAD request 200/301) → +0.05
- Profile name fuzzy-matches officer name ≥ 90% → +0.10
- Title/company in snippet matches expected entity → +0.10

**Mailing address:**

- SOS-filed address matches our input mailing address (fuzzy, city+street+zip) → +0.15
- SOS-filed address conflicts with our input → -0.10

### Final per-field score

```python
score = min(1.0, baseline + sum(validation_adjustments))
```

Cap at 1.0. Floor at 0.0 (don't produce negative scores even if adjustments subtract).

### Example: A phone for ROLATOR

Officer "Shiva Kondru" from OpenCorporates. Phone comes from Apollo.

```
baseline (Apollo)                    = 0.70
  + phonenumbers validated            = 0.05
  + line-type known (mobile)          = 0.05
  + Numverify reachable (if we use)   = 0.10
                                     -------
  phone_score                         = 0.90
```

## 4.6 Layer 2: Entity Identity Score

The per-field scores tell you how good each data point is in isolation.
Layer 2 asks: **how confident are we that this officer belongs to this LLC?**

This is where independence matters.

### Independence multipliers

Signals from the same upstream are **one** signal, not many.

```python
def independent_signal_count(sources_used: set[str]) -> int:
    groups = {
        "sos_direct": {"fl_sos", "nc_sos", "wa_sos", "ut_sos", "tx_sosdirect"},
        "sos_aggregator": {"opencorporates", "corporationwiki", "bizapedia"},
        "linkedin": {"serper_linkedin", "linkedin_scraper"},
        "b2b_enrichment": {"zoominfo", "apollo", "rocketreach"},
        "hunter_pattern": {"hunter"},
        "website": {"website_scrape"},
    }
    groups_hit = {group for group, srcs in groups.items() if sources_used & srcs}
    return len(groups_hit)
```

**Key rule:** SOS aggregators (OpenCorporates et al.) count once. Direct
SOS counts separately. If we found an officer via BOTH direct FL SOS AND
OpenCorporates, that's 2 independent signals (they might genuinely have
different data, and agreement is stronger).

### Entity identity score formula

```python
def entity_identity_score(entity):
    if not entity.sos_results:
        return 0.0  # No SOS at all — we don't know who the decision-maker is

    officer_score = max(o.score for o in entity.sos_results.officers)

    # Boost if LinkedIn confirms the person exists at this company
    if entity.has_linkedin_match:
        officer_score = min(1.0, officer_score + 0.10)

    # Boost if mailing address matches SOS-filed principal office
    if entity.mailing_matches_sos_principal_office:
        officer_score = min(1.0, officer_score + 0.05)

    # Penalty if multiple SOS candidates exist (ambiguous match)
    if len(entity.sos_results.candidates) > 1 and not entity.address_disambiguated:
        officer_score -= 0.20

    independence = independent_signal_count(entity.sources_used)

    # Scale by independence: 1 source = ×0.8, 2 = ×1.0, 3+ = ×1.1 (capped at 1.0)
    multiplier = {1: 0.8, 2: 1.0, 3: 1.05}.get(independence, 1.1)

    return max(0.0, min(1.0, officer_score * multiplier))
```

## 4.7 Layer 3: Tier Mapping

Translate entity identity score + data availability into a tier.

```python
def compute_tier(entity) -> str | None:
    identity = entity_identity_score(entity)
    independence = independent_signal_count(entity.sources_used)
    has_contact = bool(entity.final_email or entity.final_phone)

    if entity.status == "unenriched":
        return None  # Blank confidence in output CSV

    if entity.status == "failed":
        return None  # Also blank; error flag is surfaced separately

    if identity >= 0.85 and independence >= 2 and has_contact:
        return "HIGH"

    if identity >= 0.75 and independence >= 2:
        return "MEDIUM-HIGH"

    if identity >= 0.60:
        return "MEDIUM"

    return "LOW"
```

### Worked example: ROLATOR (with paid APIs plugged in)

- SOS: TX (paid, stubbed in v1 — assume found filing #0804278580)
- OpenCorporates: found same officer "Sivaramaiah Kondru"
- Serper: found LinkedIn profile matching name + "RiseCommercial Investments"
- Apollo: found `shiva@risecommercial.com` (validated MX)

```
independent_signal_count = 3  (sos_direct, sos_aggregator, linkedin, b2b_enrichment — wait, let me recount)
  - sos_direct: tx_sosdirect ✓
  - sos_aggregator: opencorporates ✓
  - linkedin: serper_linkedin ✓
  - b2b_enrichment: apollo ✓
  = 4 groups = 4 independent signals

officer_score (from TX SOSDirect filing) = 0.95
  + linkedin confirms person at company   = +0.10
  = 1.05 → capped at 1.0

multiplier (4 independent signals)         = 1.1 → capped by 1.0 anyway
entity_identity_score                      = 1.0

has_contact (Apollo email validated)       = True
tier                                       = HIGH ✓
```

### Worked example: ROLATOR (free-tier only)

- SOS: TX paid, stubbed → no result
- OpenCorporates: free tier, assume found officer
- Serper: found LinkedIn
- Apollo/Hunter: no key → no contact info

```
sources_used = {opencorporates, serper_linkedin}
independent_signal_count = 2 (sos_aggregator, linkedin)

officer_score (from OpenCorporates) = 0.75
  + linkedin confirms                 = +0.10
  = 0.85

multiplier (2 signals)                = 1.0
entity_identity_score                 = 0.85

has_contact = False (no email, no phone)
tier: identity ≥ 0.85 AND independence ≥ 2 AND has_contact → False
      identity ≥ 0.75 AND independence ≥ 2 → True
tier = MEDIUM-HIGH
```

This is the honest free-tier ceiling: **we can identify the decision-maker
with good confidence, but without paid enrichment APIs we can't provide
verified contact info.** The score correctly reflects that.

### Worked example: Unenriched entity

- SOS: nothing (new filing, or entity in a stubbed state)
- All other sources: nothing

```
status = "unenriched"
tier = None  (blank in output CSV)
```

## 4.8 Selecting the Best Value Per Field

The scope requires "up to 5 phones, 5 emails" per contact. Here's the
selection:

### Phones

```python
def select_phones(entity, max_count=5) -> list[dict]:
    candidates = []
    for source, data in entity.contacts:
        for phone_raw in data.get("phones", []):
            normalized = normalize_phone_e164(phone_raw)  # phonenumbers lib
            if not normalized:
                continue
            score = compute_phone_score(phone_raw, source, data)
            candidates.append({
                "value": normalized,
                "score": score,
                "source": source,
                "line_type": data.get("line_type"),
                "validated": data.get("validated", False),
            })

    # Dedupe by value, keep highest-scored version
    by_value = {}
    for c in candidates:
        if c["value"] not in by_value or by_value[c["value"]]["score"] < c["score"]:
            by_value[c["value"]] = c

    sorted_phones = sorted(
        by_value.values(),
        key=lambda x: x["score"],
        reverse=True,
    )
    return sorted_phones[:max_count]
```

### Emails

Same pattern. Dedupe by lowercased value. Drop role-based (`info@`,
`contact@`) only if other candidates exist. Validate with email-validator +
DNS MX.

### Mailing Address

Only ever one per entity. Prefer:

1. SOS-filed principal office address (if exists and not a registered agent address)
2. Input CSV's mailing address
3. Otherwise blank

### Decision Maker Name

Only one. Ranked officers (from SOS results) by:

1. **Title priority:** Manager / Managing Member > Member > Officer > President / CEO > Registered Agent
2. **Name quality:** has first + last name > has only last name
3. **Independence:** confirmed across multiple sources > single source

Return the winner. Other officers surfaced in the audit JSON (not in the
main output row).

### LinkedIn

One URL. Highest-scoring match. Must fuzzy-match the decision maker's name
at ≥ 90% similarity.

## 4.9 Output Row Shape (Stage 5 Contract)

Matches the 10-column output format (from the reference file), with our
simplifications:

| #   | Column              | Source                        | Notes                             |
| --- | ------------------- | ----------------------------- | --------------------------------- |
| 1   | #                   | Row index                     | Sequential                        |
| 2   | LLC Company         | `entity.entity_name_cleaned`  | Display-cased                     |
| 3   | Decision Maker Name | `entity.final_decision_maker` | "Not Found" if none               |
| 4   | Parent/Company Name | `entity.final_parent_company` | Blank in v1 (Phase 2 feature)     |
| 5   | Website             | `entity.final_website`        | From enrichment                   |
| 6   | Job Title           | `entity.final_job_title`      | From SOS officer title            |
| 7   | LinkedIn            | `entity.final_linkedin`       | URL                               |
| 8   | Email               | `entity.final_email`          | Top-scored                        |
| 9   | Phone Number        | `entity.final_phone`          | Top-scored, E.164 formatted       |
| 10  | Confidence          | `entity.final_confidence`     | HIGH/MEDIUM-HIGH/MEDIUM/LOW/blank |

The "up to 5 phones / 5 emails" requirement is fulfilled by a companion
audit JSON (see §4.10), not by extra columns in the CSV. If the client
specifically wants 5 phones + 5 emails in the main CSV, we add columns
`phone_1` through `phone_5` and `email_1` through `email_5`. Default: single
best in CSV, full list in audit. **Confirm this before Stage 5 build.**

## 4.10 Audit JSON

Alongside the output CSV, we produce `audit_<run_id>.json` containing:

- Every candidate phone and email we considered, with scores
- Every source consulted per entity
- Why each field got its tier
- Which sources failed and why

Structure:

```json
{
  "run_id": "abc-123",
  "generated_at": "2026-04-20T14:35:00Z",
  "entities": [
    {
      "entity_name": "ROLATOR & INDEPENDENCE LLC",
      "final_confidence": "MEDIUM-HIGH",
      "identity_score": 0.85,
      "independent_signals": 2,
      "sources_used": ["opencorporates", "serper_linkedin"],
      "candidates": {
        "phones": [
          {"value": "+19726791918", "score": 0.90, "source": "apollo"},
          {"value": "+19729263445", "score": 0.75, "source": "apollo"}
        ],
        "emails": [...],
        "officers": [...]
      },
      "chosen": {
        "decision_maker": "Sivaramaiah Kondru",
        "email": "shiva@risecommercial.com",
        "phone": "+19726791918",
        "linkedin": "https://linkedin.com/in/shiva-kondru-17447bab"
      },
      "rejected_candidates": [...],
      "errors": [...]
    }
  ]
}
```

The audit JSON is the reviewer's "show your work" document. If a row looks
suspicious in the CSV, they open the audit to see what we considered and why.

This is NOT exposed in the UI in v1. It's written to disk alongside the
output CSV. A Phase 2 "row drill-down" UI would read it.

## 4.11 Honest Caveats

**These scores are heuristics, not ground truth.** We calibrated them based
on reasoning and the expected-output file. After Stage 5 is built, we spot-
check against the human-curated expected output: do our HIGH-confidence rows
actually match? If mismatch rate exceeds 20%, we re-tune.

**The formula will need tweaking.** Reserve time after Stage 5 to run the
full `main_data.csv` through and compare with expected. Adjust baselines,
adjust thresholds. This is expected, not a failure.

**Free-tier ceiling is real.** Without Apollo/ZoomInfo, no entity will hit
HIGH because we can't verify contacts. MEDIUM-HIGH is the free-tier ceiling.
Document this prominently so the client understands.

## 4.12 What Step 4 Leaves to Step 5

- Fuzzy name matching algorithm (tokenize, Jaro-Winkler, thresholds)
- Disambiguation when multiple SOS filings match the name
- Ownership chain resolution (entity owned by another entity)
- Address similarity scoring (for "does this mailing match SOS-filed")

These are the inputs to Step 4's formula. Step 5 defines how we compute them.
