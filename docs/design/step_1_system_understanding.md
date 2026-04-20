# Step 1: System Understanding

## 1.1 What LeadFinder Is

LeadFinder is a **single-user local research tool** that automates what human
researchers (Jess, Maddy, Malik) currently do by hand: take a spreadsheet of
property ownership records, identify which ones are owned by businesses (LLCs,
trusts, partnerships), and figure out who the actual human decision-maker
behind each business is — along with their phone, email, LinkedIn, and
mailing address.

The enriched CSV is imported manually into GoHighLevel CRM by a human reviewer
on the client's side.

## 1.2 Scope Discipline (Important)

This is **not** a SaaS product. It has:

- No database
- No cloud storage
- No authentication
- No multi-user support (one concurrent run; HTTP 409 if a second upload arrives)
- No persistence across server restart
- No run history
- No review-in-the-UI workflow (reviewer reads the output CSV externally)

State lives in memory. Output is a temp file. One run at a time. When the
process restarts, state is gone — the user re-uploads.

The tool does three things per the scope of work:

1. **Entity Resolution** — LLC name → Managing Member / Registered Agent
2. **Confidence Scoring** — compare signals from multiple sources; flag the
   highest-confidence contact info (up to 5 phones, 5 emails, mailing address)
3. **Data Normalization** — addresses, names, phones, emails clean for CRM
   import

Everything else in the reference UI ("LeadFinder AI Pipeline") is aspirational
window dressing. The actual engine under the hood delivers the three
deliverables above.

## 1.3 What Makes This Non-Trivial

- LLCs are legally designed to obscure ownership; state disclosure rules vary.
- Free data sources are incomplete. Scraping is legally grey. Paid APIs are
  expensive.
- The same entity appears under slightly different names across sources;
  matching them requires fuzzy logic that's also conservative (a wrong match
  is worse than no match).
- 12 US states × multiple data sources × rate limits × CAPTCHAs × ToS
  considerations = lots of surface area to get wrong.

## 1.4 What Makes This Tractable

- Volume is small (50–1000 rows per run, typical 100).
- Latency budget is generous (15–20 min acceptable).
- Human review catches errors before reaching the CRM.
- The client's expected-output file shows us the quality bar.
- The client will plug in paid APIs later; we only need free tiers for the demo.

## 1.5 Success Criteria

**Functional:**

1. User uploads CSV matching the input format, clicks Start, receives a
   downloadable enriched CSV matching the output format.
2. Pipeline runs end-to-end without crashing on any edge case in the real
   341-row `main_data.csv` file.
3. At least 70% of LLC entities get some useful enrichment field populated
   (decision maker name, phone, email, or LinkedIn) — realistic for free
   tiers, below the 92% the reference UI advertises.
4. Output confidence scores are meaningful: HIGH-confidence rows are
   demonstrably more likely to be correct than LOW-confidence rows
   (spot-checkable against the expected-output file).

**Architectural:**

5. Every external data source is behind a swappable Provider interface.
   Client can plug in paid API keys via `.env` without editing code.
6. Pipeline degrades gracefully: source failure → empty field + lower
   confidence, not a crash.
7. Runs locally with `docker run` or `uvicorn app.main:app`.

**Non-criteria:**

- Matching the expected-output CSV row-for-row (that's human-curated; our ceiling, not floor)
- Coverage parity with paid-API products (Clay, Apollo, ZoomInfo)
- The editorial-voice Notes column (dropped from v1)
- Multi-user, auth, scheduled runs, in-UI review

## 1.6 The 6-Stage Pipeline

- Stage 0: Data Cleaning (SHIPPED — tests green)
- Stage 1: Run Module (SSE, state, orchestrator, 3 endpoints)
- Stage 2: Entity Parsing (search-name derivation, filing state inference)
- Stage 3: SOS Portal Lookup (scrapers + OpenCorporates fallback)
- Stage 4: Contact Enrichment (LinkedIn, email, phone discovery)
- Stage 5: Output + Scoring (confidence formula, CSV generation)

Stage 0 is complete and committed. Stages 1–5 are the remaining build.

## 1.7 Edge Case Register

From analysis of real `main_data.csv` (341 rows). Most are already handled
by Stage 0.

### Stage 0 (Cleaning) — all handled

| Case                           | Example                                    | Handling                                                    |
| ------------------------------ | ------------------------------------------ | ----------------------------------------------------------- |
| Truncated names at 30 chars    | `TREASURE VALLEY INVESTMENTS LL`           | Use `OWNER_NAME_1` as authoritative                         |
| Name-swap bug                  | `FIRST=LLLP, LAST=GEMINI`                  | Skip (data_error)                                           |
| Sentinel value                 | `NOT AVAILABLE FROM THE DATA`              | Skip (sentinel)                                             |
| Misclassified individual       | `LEE WEN-CHI`                              | Skip via token heuristic                                    |
| Trust with legal date          | `FIELDS FAMILY TRUST U/A DTD SEPT 10 2018` | TRUST keyword priority; flag `trust_with_legal_boilerplate` |
| Government entity              | `CLARK COUNTY`, `STATE OF TENN`            | Skip (government)                                           |
| Religious org                  | `SHILOH BAPTIST CHURCH`                    | Skip (religious)                                            |
| Probate estate                 | `WUNDERLICH CLARICE A ESTATE OF`           | Skip (probate)                                              |
| Cryptic abbreviation           | `OLACP-RC`                                 | Try enrichment, flag `cryptic_name`                         |
| Near-duplicates (whitespace/&) | `LONG GAME LL&C` / `LL & C`                | Normalize → merge                                           |
| Ambiguous "Foundation"         | `STOCKTON FOUNDATION INC`                  | Keep; `FOUNDATION` alone is not a skip signal               |
| PO-Box-only mailing            | `PO BOX 90` with no city                   | Flag `mailing_address_incomplete`                           |
| Same entity, many parcels      | `ROBERTSON CROSSING LLC × 15`              | Dedup; aggregate APNs                                       |

### Stages 2–4 (coming)

| Case                             | Example                                               | Handling                                              |
| -------------------------------- | ----------------------------------------------------- | ----------------------------------------------------- |
| Multi-state filing               | Mailing MD, property NC                               | Try mailing state first, then property state          |
| Delaware-filed LLCs              | Common for larger entities                            | Try DE as tertiary candidate                          |
| Same-name collision              | Two unrelated `ACME LLC` in TX and NC                 | Return all candidates, disambiguate via address match |
| Dissolved entity                 | Old filings                                           | Report status; still extract officers                 |
| LLC owned by another LLC         | Ownership chain                                       | Best-effort, one hop only                             |
| States with no member disclosure | CA, NY                                                | Return registered agent only; lower confidence        |
| Cloudflare / CAPTCHA             | ID SOS                                                | Immediate fallback to OpenCorporates                  |
| Trust (not SOS-registered)       | `JOHNSON REVOCABLE LIVING TRUST`                      | Skip SOS; go straight to search enrichment            |
| RA is service company            | `CT CORPORATION SYSTEM`                               | Low-value; prefer officer                             |
| Common officer name              | `John Smith` as member                                | Narrow by company/state; flag if ambiguous            |
| No website for email pattern     | LLC has no web presence                               | Skip email generation                                 |
| Parent company identifiable      | `AMH LANDCO BROOKSTONE LLC` → `American Homes 4 Rent` | Surface parent                                        |

### Stage 5 (Output)

| Case              | Handling                                             |
| ----------------- | ---------------------------------------------------- |
| No data found     | Output row with "Not Found" fields, blank confidence |
| Multiple contacts | Output top per field; store all in audit             |
| Conflicting data  | Prefer recent + higher independence                  |

## 1.8 Risk Register

### High risk

**R01 — Cloudflare / anti-bot on SOS portals.** Likely; observed in reference
logs. Mitigation: fall back to OpenCorporates. Tier states; be honest about
coverage.

**R02 — LinkedIn scraping detection.** Don't scrape LinkedIn directly. Use
search-engine site queries (`site:linkedin.com/in`) via Serper.

**R03 — Free-tier API exhaustion mid-run.** Track quotas in memory; when
exhausted, skip that source gracefully, drop confidence score, continue.

### Medium risk

**R04 — Entity name ambiguity (wrong match).** Worse than no match.
Mitigation: address verification; lower confidence when disambiguation fails;
human review catches egregious cases.

**R05 — Brittle scrapers (site layout changes).** Each scraper has health
checks; failure triggers fallback chain.

**R06 — CA/NY don't publish members.** Documented. Return registered agent
only; confidence explicitly reflects this.

### Lower risk

**R07 — Encoding issues in uploaded CSV.** Try UTF-8 → latin-1 fallback.

**R08 — Server restart mid-run.** User re-uploads. Acceptable for v1 scope.

**R09 — Confidence scores look arbitrary.** Document formula in
`step_4_confidence_scoring.md`; auditable.

## 1.9 Non-Goals (Explicit v1 Exclusions)

1. Skip-tracing individuals (different pipeline)
2. Deep ownership chains beyond 1 level
3. Probate estate research (needs court records)
4. Notes column generation (no editorial voice)
5. In-app review UI
6. Multi-user dashboards / authentication
7. Scheduled batch runs
8. GHL direct push (manual CSV import by user)
9. Automated CAPTCHA solving (fall back instead)
10. Address standardization via SmartyStreets (syntactic only)
11. Caching across server restarts (in-memory only)
12. 100% state coverage (tiered: TX/FL/NC/WA fully scraped; others via OC)
13. Production-grade observability (stdout logs only)
14. Run persistence / history
15. Concurrent runs (one at a time, 409 on conflict)

If the client asks for any of these: "Planned for Phase 2 when the tool
graduates to a multi-user product."

## 1.10 What Step 1 Leaves to Later Steps

- Tech stack specifics → Step 2 (Architecture)
- Pipeline flow + state → Step 3 (Pipeline Design)
- Confidence scoring formula → Step 4
- Entity matching algorithm → Step 5
- Code structure → Step 6 (Implementation Plan)
