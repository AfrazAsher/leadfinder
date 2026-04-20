# Step 3: Data Pipeline Design

## 3.1 Scope

Step 2 covered components and connections. This document traces how one
entity moves from CSV upload to enriched CSV download in the simplified
single-user architecture.

Covers: pipeline flow, concurrency, caching (in-memory), quota accounting,
retry semantics, cancellation, source independence, failure handling.

Does NOT cover: confidence scoring formula (Step 4), fuzzy entity matching
(Step 5), folder layout (Step 6).

## 3.2 Pipeline Overview

Six stages. Three batch-level (runs once per file). Three per-entity
(run concurrently, 8 at a time).

```
                ┌─────────────────────────┐
                │ Stage 0: Cleaning       │  SYNC inside POST /api/run
                │ (batch; fail-fast)      │  ~1-2 sec for 341 rows
                └───────────┬─────────────┘
                            │
                ┌───────────▼─────────────┐
                │ Stage 1: Run Setup      │  still sync in POST handler
                │ - validate              │  returns 202 + cleaning summary
                │ - init state            │
                │ - fire async task       │
                └───────────┬─────────────┘
                            │
          ╔═════════════════╪═══════════════════════╗
          ║  ASYNC BACKGROUND TASK                  ║
          ║  per-entity, 8 in parallel via semaphore║
          ║                                          ║
          ║  ┌──────────────────────┐               ║
          ║  │ Stage 2: Parsing     │ pure logic    ║
          ║  └──────────┬───────────┘               ║
          ║             ▼                            ║
          ║  ┌──────────────────────┐               ║
          ║  │ Stage 3: SOS Lookup  │ cache+quota+retry
          ║  └──────────┬───────────┘               ║
          ║             ▼                            ║
          ║  ┌──────────────────────┐               ║
          ║  │ Stage 4: Enrichment  │ cache+quota+retry
          ║  └──────────┬───────────┘               ║
          ║             ▼                            ║
          ║  [entity marked resolved/unenriched]    ║
          ╚══════════════════════════════════════════╝
                            │
                   (all entities done)
                            ▼
                ┌─────────────────────────┐
                │ Stage 5: Output         │  sync in async task
                │ - score confidence      │
                │ - generate CSV          │
                │ - write to temp file    │
                │ - emit 'done' SSE event │
                └─────────────────────────┘
```

## 3.3 Entity Lifecycle (In-Memory)

No state machine in a database. Each entity is a Python object with a
`status` field that progresses through:

```
 pending → in_progress → resolved
                       ↘ unenriched
                       ↘ failed
```

| Status        | Meaning                       | In output?              | Confidence                        |
| ------------- | ----------------------------- | ----------------------- | --------------------------------- |
| `pending`     | Created by Stage 0; queued    | No                      | —                                 |
| `in_progress` | A worker is processing it     | No                      | —                                 |
| `resolved`    | Got at least one useful field | Yes                     | HIGH / MEDIUM-HIGH / MEDIUM / LOW |
| `unenriched`  | No data found; not an error   | Yes                     | blank                             |
| `failed`      | Hard error during processing  | Yes                     | blank + `failed` flag             |
| `skipped`     | Filtered in Stage 0           | No (counted in summary) | —                                 |

**Why `unenriched` ≠ `failed`:** `unenriched` is a valid answer ("this LLC
exists but nothing found"). `failed` is an error ("something broke"). A
reviewer treats them differently.

No `cancelled` state for entities. Cancellation is global — if the run is
cancelled, the orchestrator stops picking up pending entities and exits
gracefully.

## 3.4 Run Lifecycle (In-Memory)

`app.state.current_run` is either `None` (idle) or a dict with these fields:

```python
{
  "run_id": "abc-123",
  "status": "running",         # running | completed | failed | cancelled
  "started_at": datetime,
  "current_stage": "sos_lookup",  # cleaning | parsing | sos_lookup | enrichment | output
  "entities_total": 95,
  "entities_processed": 47,
  "stats_by_stage": {...},      # for UI stage cards
  "source_stats": {...},        # coverage bars (decision makers %, phones %, ...)
  "output_path": None,          # set when Stage 5 completes
  "error_message": None,
}
```

Concurrent `POST /api/run` with `current_run != None` returns 409 per §2.5.

On process restart, `current_run` is `None`. Whatever was running is lost.
User re-uploads.

## 3.5 Worked Example: ROLATOR & INDEPENDENCE LLC

### T=0: User Uploads `main_data.csv`

`POST /api/run` with multipart form.

**If `app.state.current_run is not None`:** return 409 with current run info.
**Else:** proceed.

### T=0.1s: Save Upload to Temp File

```python
upload_path = tempfile.NamedTemporaryFile(
    suffix=".csv", delete=False
).name
# write uploaded bytes to upload_path
```

### T=0.2s – T=2s: Stage 0 Runs Synchronously

Cleaning runs on the CSV. Produces 95 unique entities.

If cleaning fails (missing columns, malformed CSV): return
`400 Bad Request` with specific error. No state created. User re-uploads
with corrected file.

If cleaning succeeds:

```python
app.state.current_run = {
    "run_id": str(uuid4())[:8],
    "status": "running",
    "started_at": datetime.utcnow(),
    "current_stage": "parsing",
    "entities_total": 95,
    "entities_processed": 0,
    "stats_by_stage": {...},
    "source_stats": {...},
    "output_path": None,
    "error_message": None,
}
app.state.entities = [cleaning_report.entities]  # list of CleanedEntity
```

### T=2s: Return Response

```json
{
  "run_id": "abc-123",
  "status": "running",
  "cleaning_summary": {
    "input_rows": 341,
    "entities_kept": 95,
    "skipped": {
      "individuals": 111,
      "government": 3,
      "religious": 2,
      "probate": 1,
      "sentinel": 1,
      "data_error": 3,
      "misclassified_individual": 5
    }
  }
}
```

### T=2.1s: Fire Background Task

```python
app.state.current_task = asyncio.create_task(
    orchestrate(app.state.current_run["run_id"])
)
```

The task runs Stages 2–5 in the background. The HTTP response is already
sent; the user's browser opens SSE to `/api/events` to stream progress.

### T=2.2s: Orchestrator Starts

```python
async def orchestrate(run_id: str):
    try:
        emit_log(f"[{ts}] [ORCH] Starting pipeline for run {run_id}")
        await emit_stage("parsing")

        semaphore = asyncio.Semaphore(settings.max_parallel_entities)

        async def work(entity):
            async with semaphore:
                if cancellation_requested():
                    return
                await parse(entity)          # Stage 2
                await sos_lookup(entity)     # Stage 3
                await enrich(entity)         # Stage 4
                app.state.current_run["entities_processed"] += 1
                await emit_stats()

        await asyncio.gather(*[
            work(e) for e in app.state.entities
        ])

        await emit_stage("output")
        await run_stage_5()                  # Stage 5: scoring + CSV

        app.state.current_run["status"] = "completed"
        await emit_done()

    except Exception as e:
        app.state.current_run["status"] = "failed"
        app.state.current_run["error_message"] = str(e)
        emit_log(f"[ERROR] Pipeline failed: {e}")
```

### T=2.3s: ROLATOR Enters Stage 2

Pure logic, no external calls:

```python
entity.search_names = [
    "ROLATOR & INDEPENDENCE LLC",
    "ROLATOR AND INDEPENDENCE LLC",
    "ROLATOR INDEPENDENCE LLC",
]
entity.filing_state_candidates = ["TX", "DE"]
entity.status = "in_progress"
```

### T=3s: Stage 3 (SOS Lookup)

```python
sos_result = None
for state in entity.filing_state_candidates:  # ["TX", "DE"]
    # 1. In-memory cache lookup
    key = make_cache_key("sos", state, entity.entity_name_normalized)
    cached = cache_get(key)
    if cached:
        sos_result = cached
        break

    # 2. Quota check
    if not try_consume_quota(f"sos_{state.lower()}"):
        emit_log(f"[QUOTA] {state} SOS exhausted, skipping")
        continue

    # 3. External call with retry + jitter
    try:
        sos_result = await providers["sos"][state].search(entity.search_names)
        cache_put(key, sos_result, ttl=timedelta(days=30))
        break
    except ScraperBlocked:
        emit_log(f"[WARN] {state} SOS blocked, falling through")
        continue

# No OpenCorporates fallback in v1 — if all state scrapers fail,
# entity becomes `unenriched`. OC is deferred to Stage 3c as an
# optional last-resort fallback for rare cases where state scraping
# returns nothing for all candidate states.

entity.sos_results = sos_result or []
entity.sos_source = source_label  # e.g., "FL_direct" or "OpenCorporates_fallback"
```

For ROLATOR on free tier: TX SOSDirect is paid, so we probably hit
OpenCorporates fallback (if a key is configured) or return empty and continue.

### T=5s: Stage 4 (Contact Enrichment)

With officer names from Stage 3, fan out:

```python
contacts = []
for officer in sos_result.officers:
    # LinkedIn via Serper search
    if try_consume_quota("serper"):
        result = await providers["serper"].search(
            query=f'"{officer.name}" "{entity.entity_name_cleaned}" site:linkedin.com/in'
        )
        contacts.append({"source": "linkedin_serper", "data": result})

    # Email via Hunter
    if website and try_consume_quota("hunter"):
        result = await providers["hunter"].find_email(
            first_name=officer.first_name,
            last_name=officer.last_name,
            domain=website,
        )
        contacts.append({"source": "hunter", "data": result})

    # Apollo (if key configured)
    if settings.apollo_api_key and try_consume_quota("apollo"):
        result = await providers["apollo"].enrich_person(
            name=officer.name, company=entity.entity_name_cleaned
        )
        contacts.append({"source": "apollo", "data": result})

entity.contacts = contacts
entity.status = "resolved" if contacts else "unenriched"
```

### T=7s: Entity Done

```python
app.state.current_run["entities_processed"] += 1
await emit_stats()
```

### T=7s to ~T=900s: Other 94 Entities Process

8 workers drain the list via semaphore. SSE emits log lines and stats
continuously. Browser log window updates live.

### T=~900s: Stage 5 (Output)

Runs once, after all entities are done:

```python
for entity in app.state.entities:
    if entity.status == "resolved":
        entity.final_decision_maker = pick_best_officer(entity)
        entity.final_email = pick_best_email(entity)
        entity.final_phone = pick_best_phone(entity)
        entity.final_linkedin = pick_best_linkedin(entity)
        entity.final_confidence = score_confidence(entity)  # Step 4 formula

# Generate CSV
output_path = tempfile.NamedTemporaryFile(
    suffix=".csv", delete=False
).name
write_csv(app.state.entities, output_path)

app.state.current_run["output_path"] = output_path
app.state.current_run["status"] = "completed"
app.state.current_run["current_stage"] = "output_complete"
await emit_done()
```

### T=~900s: User Downloads

Frontend's SSE stream receives the `done` event. Download button becomes
enabled. User clicks → `GET /api/download` reads from
`app.state.current_run["output_path"]` and streams as
`text/csv` attachment.

## 3.6 Concurrency Model

**Pool size: 8.**

```python
semaphore = asyncio.Semaphore(settings.max_parallel_entities)

async def process_entity(entity):
    async with semaphore:
        if cancellation_requested():
            return
        await parse(entity)
        await sos_lookup(entity)
        await enrich(entity)

await asyncio.gather(*[process_entity(e) for e in app.state.entities])
```

**Tunable:** `MAX_PARALLEL_ENTITIES` env var, default 8.

**Rationale:**

- Each Playwright browser context ~150 MB; 8 × 150 MB = 1.2 GB — fits
  Docker containers on modest hardware
- Most SOS sites rate-limit below 8 concurrent requests per IP
- 8 "looks human-ish" to Cloudflare bot detection

**Gotcha:** `asyncio.gather` schedules all 95 coroutines at once, but only 8
actually run (the rest await the semaphore). This is correct. Don't batch
manually.

## 3.7 Caching Strategy (In-Memory)

```python
app.state.cache: dict[str, tuple[Any, datetime]] = {}

def cache_get(key: str) -> Any | None:
    entry = app.state.cache.get(key)
    if not entry:
        return None
    value, expires_at = entry
    if datetime.utcnow() > expires_at:
        del app.state.cache[key]
        return None
    return value

def cache_put(key: str, value: Any, ttl: timedelta):
    app.state.cache[key] = (value, datetime.utcnow() + ttl)
```

### Cache Key Formula

```python
key = hashlib.sha256(
    f"{source}|{entity_name_normalized}|{state}|{query_type}".encode()
).hexdigest()
```

### TTLs by Source

| Source          | TTL     |
| --------------- | ------- |
| SOS lookups     | 30 days |
| Hunter / Apollo | 14 days |
| Serper (search) | 7 days  |
| Empty results   | 7 days  |

TTLs are symbolic on a single-run basis (process restart clears everything).
They matter when one run contains duplicate-ish entities or when a user
re-uploads after a failed run within the same process session.

### Negative Caching

Empty results are cached (with shorter TTL) to prevent hammering dead lookups
within a single run.

### Force Refresh (Phase 2 Consideration)

Not implemented in v1. Every run starts with a cold cache per process restart
anyway, so "force refresh" is implicit.

## 3.8 Quota Management (In-Memory)

```python
app.state.quotas: dict[str, dict] = {
    "serper": {"calls_made": 0, "calls_limit": 2500},
    "hunter": {"calls_made": 0, "calls_limit": 25},
    "apollo": {"calls_made": 0, "calls_limit": 60},
}

async def try_consume_quota(source: str, amount: int = 1) -> bool:
    async with app.state.quota_lock:  # asyncio.Lock
        q = app.state.quotas.get(source)
        if not q:
            return True  # unknown source, allow
        if q["calls_made"] + amount > q["calls_limit"]:
            return False
        q["calls_made"] += amount
        return True
```

**Why `asyncio.Lock` instead of `FOR UPDATE`:** single process, in-memory
state. An asyncio lock serializes access across coroutines without any DB.

**UI surfacing:** when a quota hits 0, orchestrator emits an SSE event; UI
shows a warning badge on that source's card. Run continues.

**Quota reset on process restart** is a known limitation (§2.9). Acceptable
for demo.

## 3.9 Retry Semantics

```python
from tenacity import (
    retry, stop_after_attempt, wait_random_exponential,
    retry_if_exception_type,
)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=2, min=2, max=10),
    retry=retry_if_exception_type((
        httpx.TimeoutException,
        httpx.NetworkError,
        PlaywrightTimeoutError,
    )),
    reraise=True,
)
async def call_provider(...):
    ...
```

### What Retries

- HTTP 5xx, 429, 503
- Network timeouts, DNS failures
- Playwright page load / element-wait timeouts

### What Does Not Retry

- HTTP 401/403 (auth won't fix itself)
- HTTP 404 (entity genuinely not in DB)
- Cloudflare challenge (same IP won't help; fall back)
- Parse errors (site layout changed; needs code fix)

### Backoff With Jitter

Random exponential [2s, 10s]. Jitter prevents thundering herd when multiple
workers hit a 429 simultaneously.

### Critical Rule: Retries Are Within a Provider, Not Across

3 retries on TX SOS fail → fall through to OpenCorporates, don't retry
the chain.

## 3.10 Cancellation

`DELETE /api/run` — cancels the current run.

```python
@router.delete("/api/run")
async def cancel_run():
    run = app.state.current_run
    if not run or run["status"] != "running":
        return {"status": "no active run"}

    run["status"] = "cancelled"  # flagged via in-memory state

    task = app.state.current_task
    if task and not task.done():
        task.cancel()

    emit_log("[CANCEL] Run cancelled by user")
    return {"status": "cancelled"}
```

Orchestrator checks `cancellation_requested()` between entities:

```python
def cancellation_requested() -> bool:
    run = app.state.current_run
    return run is None or run.get("status") == "cancelled"
```

Cancellation doesn't interrupt mid-Playwright-page-load. It stops picking
up new entities. Current in-flight entities finish naturally.

No partial output CSV is generated on cancel.

## 3.11 Failure Handling

| Failure                        | Where          | User Impact                                                   |
| ------------------------------ | -------------- | ------------------------------------------------------------- |
| Cloudflare blocks a state SOS  | Stage 3        | Fall through to OpenCorporates → lower confidence             |
| Hunter quota exhausted         | Stage 4        | SSE emits warning; remaining entities skip email lookup       |
| OpenCorporates 500             | Stage 3        | 3 retries → fall through; entity has empty sos_results        |
| Playwright OOM                 | Stage 3        | Retry with fresh browser; repeat OOM → entity marked `failed` |
| Per-entity unhandled exception | Any stage      | Entity marked `failed`, run continues                         |
| Orchestrator-level exception   | Any            | Run marked `failed`, SSE emits error, task ends               |
| User clicks cancel             | Via API        | Orchestrator stops between entities                           |
| Malformed CSV                  | Stage 0 (sync) | POST returns 400, no state created                            |
| Process crash                  | Any            | State lost; user re-uploads (acceptable for v1)               |

**Design principle:** one entity's failure doesn't kill the run. Blast radius
of any failure = one row in the output CSV, marked `failed`, visible to the
reviewer.

## 3.12 Source Independence (Prep for Step 4)

Confidence scoring counts **independent** signals, not total sources.

```
SOS filings (ground truth)
    ├── FL Sunbiz (direct — Stage 3a)
    ├── NC SOS (direct — Stage 3a)
    ├── WA UBI (direct — Stage 3b)
    └── UT Business Search (direct — Stage 3b)

Search (LinkedIn discovery)
    └── Serper (Google-proxy search)

Contact intel
    ├── Hunter (email patterns)
    ├── Apollo (crowdsourced, if key provided)
    └── ZoomInfo, RocketReach (stubbed v1)
```

**Example:**

- FL Sunbiz finds "Jackson Su, Managing Member of Bridgetower R15 Owner LLC"
  AND Serper confirms LinkedIn profile matching = **2 independent** signals
  → HIGH
- OpenCorporates + CorporationWiki both show "Jackson Su" = **1 signal**
  (both from SOS upstream) → LOW unless confirmed elsewhere

Step 4 formalizes this as a weighted score with independence multipliers.

## 3.13 Non-Obvious Design Choices (Defended)

### Stage 0 sync inside POST, Stages 2–5 async

Fail-fast on bad CSV (immediate 400) vs polling-and-discovering-failure.

### In-memory cache + quotas

No DB. `asyncio.Lock` for concurrency. Reset on process restart. Acceptable
for single-user local tool.

### Per-entity `resolved` vs `unenriched` vs `failed`

Real information for the reviewer. Different actions for each.

### SSE over WebSockets

One-way server → browser streaming. Simpler. Native browser support via
`EventSource`. Reconnection is "refresh the page" in v1.

### No persistence

Explicitly scoped out. Every bit of persistence is a failure mode to
handle. V1 is a research tool, not a system of record.

### Task registry (single entry)

`app.state.current_task` is just one task. No dict needed because only one
run exists at a time. If it's set and not done, we're busy (409 path).

## 3.14 What Step 3 Leaves to Later Steps

- Exact confidence scoring formula → Step 4
- Fuzzy name matching + same-name collision disambiguation → Step 5
- Folder layout, file-by-file responsibility → Step 6
- Frontend UI flow details → frontend spec (late in build)
- OpenCorporates fallback (Stage 3c; optional, only if state scrapers show real coverage gaps)
