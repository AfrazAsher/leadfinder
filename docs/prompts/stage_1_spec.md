I'm building Stage 1 (Run Module) for LeadFinder — a pipeline that takes LLC property ownership records and enriches them with decision-maker contact info. Stages 0 and 2 are already shipped and committed.

This prompt builds Stage 1: the HTTP surface, in-memory state, SSE streaming, orchestrator, and glue code that turns the existing pure-function pipeline stages into a running application. Stages 3, 4, and 5 are NOT yet built — use stubs for them that I describe below.

## Environment context

- Project root: repo with `venv/` at the project root (Python 3.10)
- Activate venv: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Unix)
- Backend at `backend/`. Existing tests must still pass.
- Stage 0: `backend/app/pipeline/stage_0_cleaning.py` (clean_csv function returning CleaningReport)
- Stage 2: `backend/app/pipeline/stage_2_parsing.py` (parse_entity function)
- Expected test count before your changes: 23 passing (10 Stage 0 + 13 Stage 2)

## Project context — read FIRST if anything below seems ambiguous

See `docs/design/` for full design context:

- `step_1_system_understanding.md` — scope, non-goals
- `step_2_architecture.md` — in-memory state, 3 endpoints
- `step_3_pipeline_design.md` — orchestrator, concurrency, SSE events

Key principles:

1. **Single user, single process, single run.** One pipeline runs at a time. A second concurrent POST returns 409 with informative error.
2. **No database, no cloud storage, no auth.** State lives in `app.state` (in-memory). Output CSV is a temp file.
3. **No persistence across process restart.** User re-uploads.
4. **SSE for live logs**, not polling.

If anything in this prompt contradicts the design docs, ASK before coding.

## What Stage 1 Does

Builds 4 HTTP endpoints wiring together the pipeline:

```
POST   /api/run       Upload CSV, run Stage 0 sync, fire async background task
GET    /api/events    SSE stream of log lines and state events
GET    /api/download  Stream the output CSV when complete
DELETE /api/run       Cancel the running pipeline
GET    /health        (Already exists — don't touch)
```

Plus:

- In-memory state container (`app.state`)
- SSE broadcast mechanism (any log line goes to all connected subscribers)
- Orchestrator that runs Stages 2 → 3 → 4 → 5 for each entity concurrently (8 at a time via semaphore)
- Stub implementations of Stages 3, 4, 5 (placeholder functions that mark entities as `unenriched` and emit clear "stubbed" log lines, so the full flow is testable end-to-end)
- Structured logging via structlog that ALSO pushes to the SSE stream

## Files to Create / Modify

### Create

```
backend/app/state.py                         NEW — app-level state container + helpers
backend/app/events.py                        NEW — SSE broadcast + log sink
backend/app/routers/run.py                   NEW — POST/DELETE /api/run
backend/app/routers/events.py                NEW — GET /api/events
backend/app/routers/download.py              NEW — GET /api/download
backend/app/pipeline/stage_3_sos.py          NEW — STUB (returns empty sos_results)
backend/app/pipeline/stage_4_enrichment.py   NEW — STUB (returns empty contacts)
backend/app/pipeline/stage_5_output.py       NEW — REAL (generates CSV from in-memory entities)
backend/app/pipeline/orchestrator.py         NEW — glue that runs Stages 2–5 per entity
backend/app/models/run.py                    NEW — RunState Pydantic model
backend/tests/test_stage_1_run.py            NEW — HTTP + SSE integration tests
backend/tests/test_orchestrator.py           NEW — orchestrator unit tests
```

### Modify

```
backend/app/main.py                          MODIFY — wire routers, init app.state
backend/app/models/entity.py                 MODIFY — add runtime fields (status, sos_results, contacts, final_*)
```

### Do NOT touch

```
backend/app/pipeline/stage_0_cleaning.py
backend/app/pipeline/stage_2_parsing.py
backend/app/routers/health.py
backend/tests/test_stage_0_cleaning.py
backend/tests/test_stage_2_parsing.py
backend/tests/fixtures/ (leave existing fixtures intact; add new ones alongside if needed)
backend/tests/conftest.py (leave existing fixtures)
```

## Model Changes (entity.py)

Add these runtime fields to `CleanedEntity`. Default values should be non-None and safe so existing Stage 0 / Stage 2 tests don't break.

```python
# backend/app/models/entity.py — ADD these fields to CleanedEntity
# Add them AFTER quality_flags, BEFORE the end of the class.

    # --- Stage 3 (SOS Lookup) output ---
    status: str = "pending"  # pending | in_progress | resolved | unenriched | failed
    sos_results: list[dict] = []  # raw from providers, see Step 3 design doc
    sos_source: Optional[str] = None  # e.g., "fl_direct" or "opencorporates_fallback"

    # --- Stage 4 (Enrichment) output ---
    contacts: list[dict] = []  # raw from enrichment providers

    # --- Stage 5 (Output) output ---
    final_decision_maker: Optional[str] = None
    final_parent_company: Optional[str] = None
    final_website: Optional[str] = None
    final_job_title: Optional[str] = None
    final_linkedin: Optional[str] = None
    final_email: Optional[str] = None
    final_phone: Optional[str] = None
    final_confidence: Optional[str] = None  # HIGH | MEDIUM-HIGH | MEDIUM | LOW | None
    error_message: Optional[str] = None  # populated when status == "failed"
```

Verify `test_stage_0_cleaning.py` and `test_stage_2_parsing.py` still pass after this change — they should, since all new fields have defaults.

## New Model: RunState

```python
# backend/app/models/run.py — NEW FILE
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class RunState(BaseModel):
    """In-memory state of the current (or most recently completed) pipeline run."""
    run_id: str
    status: str  # pending | running | completed | failed | cancelled
    started_at: datetime
    completed_at: Optional[datetime] = None
    current_stage: str  # queued | cleaning | parsing | sos_lookup | enrichment | output | output_complete
    entities_total: int = 0
    entities_processed: int = 0
    entities_resolved: int = 0
    entities_unenriched: int = 0
    entities_failed: int = 0
    cleaning_summary: Optional[dict] = None
    output_path: Optional[str] = None  # temp file path, set when Stage 5 completes
    error_message: Optional[str] = None


class RunProgressSnapshot(BaseModel):
    """Subset of RunState safe to emit over SSE and return in 409 response."""
    run_id: str
    status: str
    started_at: datetime
    current_stage: str
    entities_total: int
    entities_processed: int
    elapsed_seconds: float
```

## In-Memory State (app/state.py)

```python
# backend/app/state.py — NEW FILE
import asyncio
from collections import deque
from typing import Optional
from app.models.entity import CleanedEntity
from app.models.run import RunState


class AppState:
    """
    In-memory, single-process state container.
    Lives on FastAPI's app.state.pipeline (see main.py).
    Gone on process restart. This is by design (see step_2_architecture.md).
    """
    def __init__(self):
        # Current or most recently completed run. None means idle (server just started).
        self.current_run: Optional[RunState] = None

        # Entities for the current run. Replaced on each new run (not persistent).
        self.entities: list[CleanedEntity] = []

        # Running asyncio.Task for the active orchestrator (None when idle).
        # Set by router, cancelled on DELETE /api/run.
        self.current_task: Optional[asyncio.Task] = None

        # Rolling log buffer for late SSE subscribers — they get this as backfill.
        self.log_buffer: deque[dict] = deque(maxlen=500)

        # Live SSE subscribers — each has its own queue; broadcast fans out.
        self.sse_subscribers: set[asyncio.Queue] = set()

        # In-memory cache (used by Stages 3, 4 later). Now just initialized empty.
        self.cache: dict[str, tuple] = {}  # key -> (value, expires_at)

        # In-memory quotas. Initialized with sane defaults at app startup.
        self.quotas: dict[str, dict] = {}

        # Lock for quota operations (asyncio context).
        self.quota_lock = asyncio.Lock()

    def is_busy(self) -> bool:
        """True if a run is currently in progress."""
        if self.current_run is None:
            return False
        return self.current_run.status == "running"

    def reset_for_new_run(self):
        """Clear previous run's entities and reset counters. Called on new POST /api/run."""
        self.entities = []
        self.log_buffer.clear()
        # Note: we don't clear cache or quotas — those persist across runs within a process.


def init_quotas(state: AppState) -> None:
    """Seed default quota limits. Called at app startup."""
    state.quotas = {
        "opencorporates": {"calls_made": 0, "calls_limit": 200},
        "serper":         {"calls_made": 0, "calls_limit": 2500},
        "hunter":         {"calls_made": 0, "calls_limit": 25},
        "apollo":         {"calls_made": 0, "calls_limit": 60},
    }
```

## SSE Mechanism (app/events.py)

```python
# backend/app/events.py — NEW FILE
import asyncio
import json
from datetime import datetime
from typing import Any, AsyncIterator
from app.state import AppState


async def broadcast(
    state: AppState,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """
    Push an event to all current SSE subscribers AND append to the rolling log buffer.

    event_type is one of:
        "log"    — {"level": "INFO"|"WARN"|"ERROR", "message": str, "timestamp": iso8601}
        "stats"  — {"entities_processed": int, "entities_total": int, ...snapshot of RunState}
        "stage"  — {"stage": str, "done": bool}
        "done"   — {"output_path": str, "final_stats": {...}}
        "error"  — {"message": str}
    """
    payload = {
        "event": event_type,
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    }
    # Add to buffer for late-subscribers
    state.log_buffer.append(payload)

    # Fan out to subscribers (non-blocking; dropped subscribers logged but not fatal)
    for queue in list(state.sse_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # subscriber is slow; drop the event for them rather than blocking the producer
            pass


async def subscribe(state: AppState) -> AsyncIterator[str]:
    """
    Async generator yielding formatted SSE strings for one subscriber.
    Replays the rolling buffer first (backfill), then streams new events.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    state.sse_subscribers.add(queue)

    try:
        # Backfill first
        for payload in list(state.log_buffer):
            yield format_sse(payload)

        # Live stream
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield format_sse(payload)
                # Close gracefully on "done" or "error" event type
                if payload["event"] in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                # Keep-alive ping every 15s so proxies don't drop the connection
                yield ":keepalive\n\n"
    finally:
        state.sse_subscribers.discard(queue)


def format_sse(payload: dict) -> str:
    """Format a payload as a proper SSE frame."""
    data_json = json.dumps(payload)
    return f"event: {payload['event']}\ndata: {data_json}\n\n"


async def emit_log(state: AppState, level: str, message: str) -> None:
    """Convenience wrapper for log events."""
    await broadcast(state, "log", {
        "level": level,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    })
```

## POST /api/run

```python
# backend/app/routers/run.py — NEW FILE
import asyncio
import tempfile
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from app.state import AppState
from app.models.run import RunState, RunProgressSnapshot
from app.pipeline.stage_0_cleaning import clean_csv
from app.pipeline.orchestrator import orchestrate
from app.events import broadcast, emit_log

router = APIRouter()


@router.post("/api/run")
async def start_run(request: Request, file: UploadFile = File(...)):
    state: AppState = request.app.state.pipeline

    # 1. 409 if busy
    if state.is_busy():
        snapshot = _progress_snapshot(state)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "run_in_progress",
                "message": (
                    f"A pipeline run is currently in progress "
                    f"(started {int(snapshot.elapsed_seconds)}s ago, "
                    f"stage: {snapshot.current_stage}). "
                    f"Please wait for it to finish before starting a new run."
                ),
                "current_run": snapshot.model_dump(mode="json"),
            },
        )

    # 2. Save upload to temp file
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    upload_path = tempfile.NamedTemporaryFile(
        mode="wb", suffix=".csv", delete=False
    ).name
    try:
        with open(upload_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to save upload: {e}")

    # 3. Run Stage 0 synchronously (fail-fast on bad CSV)
    try:
        report = clean_csv(upload_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"CSV validation failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleaning failed: {e}")

    # 4. Initialize state for new run
    state.reset_for_new_run()
    run_id = uuid.uuid4().hex[:8]
    state.current_run = RunState(
        run_id=run_id,
        status="running",
        started_at=datetime.now(timezone.utc),
        current_stage="parsing",
        entities_total=len(report.entities),
        cleaning_summary={
            "input_rows": report.input_rows,
            "entities_kept": report.unique_entities,
            "skipped": {
                "individuals": report.individuals_skipped,
                "government": report.government_skipped,
                "religious": report.religious_skipped,
                "probate": report.probate_skipped,
                "sentinel": report.sentinel_skipped,
                "data_error": report.data_error_skipped,
                "misclassified_individual": report.misclassified_individual_skipped,
            },
        },
    )
    state.entities = list(report.entities)  # shallow copy list; objects shared

    # 5. Fire background task
    state.current_task = asyncio.create_task(orchestrate(state))

    # 6. Emit initial log event
    await emit_log(state, "INFO", f"Run {run_id} started with {len(report.entities)} entities")

    # 7. Return 202 Accepted with cleaning summary
    return {
        "run_id": run_id,
        "status": "running",
        "cleaning_summary": state.current_run.cleaning_summary,
    }


@router.delete("/api/run")
async def cancel_run(request: Request):
    state: AppState = request.app.state.pipeline

    if not state.current_run or state.current_run.status != "running":
        return {"status": "no_active_run"}

    state.current_run.status = "cancelled"
    await emit_log(state, "WARN", "Run cancelled by user")

    if state.current_task and not state.current_task.done():
        state.current_task.cancel()

    return {"status": "cancelled", "run_id": state.current_run.run_id}


def _progress_snapshot(state: AppState) -> RunProgressSnapshot:
    r = state.current_run
    assert r is not None
    elapsed = (datetime.now(timezone.utc) - r.started_at).total_seconds()
    return RunProgressSnapshot(
        run_id=r.run_id,
        status=r.status,
        started_at=r.started_at,
        current_stage=r.current_stage,
        entities_total=r.entities_total,
        entities_processed=r.entities_processed,
        elapsed_seconds=elapsed,
    )
```

## GET /api/events (SSE)

```python
# backend/app/routers/events.py — NEW FILE
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.state import AppState
from app.events import subscribe

router = APIRouter()


@router.get("/api/events")
async def events(request: Request):
    state: AppState = request.app.state.pipeline

    async def event_generator():
        async for sse_frame in subscribe(state):
            if await request.is_disconnected():
                break
            yield sse_frame

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # for nginx, harmless elsewhere
            "Connection": "keep-alive",
        },
    )
```

## GET /api/download

```python
# backend/app/routers/download.py — NEW FILE
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from app.state import AppState

router = APIRouter()


@router.get("/api/download")
async def download(request: Request):
    state: AppState = request.app.state.pipeline

    if not state.current_run:
        raise HTTPException(status_code=404, detail="No run has been started yet")

    if state.current_run.status != "completed":
        raise HTTPException(
            status_code=404,
            detail=f"Output not available; run status is '{state.current_run.status}'",
        )

    output_path = state.current_run.output_path
    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=500, detail="Output file is missing")

    filename = f"leadfinder_enriched_{state.current_run.run_id}.csv"
    return FileResponse(
        path=output_path,
        media_type="text/csv",
        filename=filename,
    )
```

## Orchestrator

```python
# backend/app/pipeline/orchestrator.py — NEW FILE
import asyncio
from datetime import datetime, timezone
from app.state import AppState
from app.events import emit_log, broadcast
from app.pipeline.stage_2_parsing import parse_entity
from app.pipeline.stage_3_sos import lookup_sos
from app.pipeline.stage_4_enrichment import enrich_contacts
from app.pipeline.stage_5_output import generate_output
from app.core.config import settings


async def orchestrate(state: AppState) -> None:
    """Main async task that runs Stages 2 → 3 → 4 (per entity, 8 at a time), then Stage 5."""
    try:
        await emit_log(state, "INFO", f"Orchestrator started for run {state.current_run.run_id}")

        semaphore = asyncio.Semaphore(settings.max_parallel_entities)

        async def process_one(entity):
            async with semaphore:
                if _cancelled(state):
                    return
                try:
                    entity.status = "in_progress"
                    parse_entity(entity)  # sync, pure — Stage 2
                    if _cancelled(state):
                        return
                    await lookup_sos(state, entity)  # Stage 3 (stubbed for now)
                    if _cancelled(state):
                        return
                    await enrich_contacts(state, entity)  # Stage 4 (stubbed for now)

                    # Promote to terminal status (stubs leave entity without contacts → unenriched)
                    if entity.status == "in_progress":
                        if entity.contacts or entity.sos_results:
                            entity.status = "resolved"
                        else:
                            entity.status = "unenriched"
                except Exception as e:
                    entity.status = "failed"
                    entity.error_message = str(e)
                    await emit_log(state, "ERROR",
                        f"Entity {entity.entity_name_cleaned} failed: {e}")
                finally:
                    state.current_run.entities_processed += 1
                    if entity.status == "resolved":
                        state.current_run.entities_resolved += 1
                    elif entity.status == "unenriched":
                        state.current_run.entities_unenriched += 1
                    elif entity.status == "failed":
                        state.current_run.entities_failed += 1
                    await _emit_stats(state)

        # --- Stages 2-4 per entity ---
        state.current_run.current_stage = "processing"
        await broadcast(state, "stage", {"stage": "processing", "done": False})
        await asyncio.gather(*[process_one(e) for e in state.entities])

        if _cancelled(state):
            await emit_log(state, "WARN", "Pipeline cancelled; skipping output stage")
            state.current_run.completed_at = datetime.now(timezone.utc)
            return

        # --- Stage 5: output ---
        state.current_run.current_stage = "output"
        await broadcast(state, "stage", {"stage": "output", "done": False})
        await emit_log(state, "INFO", "Generating output CSV and audit JSON")
        output_path = await generate_output(state)
        state.current_run.output_path = output_path

        # --- Finish ---
        state.current_run.current_stage = "output_complete"
        state.current_run.status = "completed"
        state.current_run.completed_at = datetime.now(timezone.utc)

        await emit_log(state, "INFO",
            f"Pipeline completed: {state.current_run.entities_resolved} resolved, "
            f"{state.current_run.entities_unenriched} unenriched, "
            f"{state.current_run.entities_failed} failed")
        await broadcast(state, "done", {
            "output_path": output_path,
            "run_id": state.current_run.run_id,
            "final_stats": {
                "resolved": state.current_run.entities_resolved,
                "unenriched": state.current_run.entities_unenriched,
                "failed": state.current_run.entities_failed,
                "total": state.current_run.entities_total,
            },
        })

    except asyncio.CancelledError:
        state.current_run.status = "cancelled"
        state.current_run.completed_at = datetime.now(timezone.utc)
        await emit_log(state, "WARN", "Orchestrator cancelled")
        raise
    except Exception as e:
        state.current_run.status = "failed"
        state.current_run.error_message = str(e)
        state.current_run.completed_at = datetime.now(timezone.utc)
        await emit_log(state, "ERROR", f"Orchestrator failed: {e}")
        await broadcast(state, "error", {"message": str(e)})


def _cancelled(state: AppState) -> bool:
    return state.current_run is None or state.current_run.status == "cancelled"


async def _emit_stats(state: AppState) -> None:
    r = state.current_run
    await broadcast(state, "stats", {
        "run_id": r.run_id,
        "status": r.status,
        "current_stage": r.current_stage,
        "entities_total": r.entities_total,
        "entities_processed": r.entities_processed,
        "entities_resolved": r.entities_resolved,
        "entities_unenriched": r.entities_unenriched,
        "entities_failed": r.entities_failed,
    })
```

## Stage Stubs

```python
# backend/app/pipeline/stage_3_sos.py — NEW FILE (STUB)
"""
Stage 3: Secretary of State Portal Lookup — STUB.

Real implementation will query state SOS portals (FL Sunbiz, NC SOS, WA UBI,
UT Business Search, TX SOSDirect) and fall back to OpenCorporates. For now,
this stub emits a log line and returns no results so the pipeline can run
end-to-end.
"""
from app.state import AppState
from app.events import emit_log


async def lookup_sos(state: AppState, entity) -> None:
    await emit_log(
        state, "INFO",
        f"[SOS stub] Would query {entity.filing_state_candidates} for "
        f"{entity.entity_name_search!r} — no providers wired yet"
    )
    # Stub: leave sos_results = [] and sos_source = None.
    # Entity will be marked `unenriched` in orchestrator.
```

```python
# backend/app/pipeline/stage_4_enrichment.py — NEW FILE (STUB)
"""
Stage 4: Contact Enrichment — STUB.

Real implementation will search LinkedIn via Serper, resolve emails via
Hunter/Apollo, and collect phones from enrichment APIs. For now, this stub
emits a log line and returns no contacts.
"""
from app.state import AppState
from app.events import emit_log


async def enrich_contacts(state: AppState, entity) -> None:
    await emit_log(
        state, "INFO",
        f"[Enrich stub] Would enrich contacts for {entity.entity_name_search!r} "
        f"— no providers wired yet"
    )
    # Stub: leave contacts = [].
```

## Stage 5: Real Output Generation (minimal — full confidence scoring comes later)

```python
# backend/app/pipeline/stage_5_output.py — NEW FILE
"""
Stage 5: Output Generation.

In Phase 1 (stubs): generates a 10-column CSV with entity name, skipped
reasons, and 'Not Found' for unpopulated fields. Full confidence scoring
lands when Stages 3 and 4 are wired up.
"""
import csv
import tempfile
from pathlib import Path
from app.state import AppState
from app.events import emit_log


OUTPUT_COLUMNS = [
    "#",
    "LLC Company",
    "Decision Maker Name",
    "Parent/Company Name",
    "Website",
    "Job Title",
    "LinkedIn",
    "Email",
    "Phone Number",
    "Confidence",
]


async def generate_output(state: AppState) -> str:
    """Write output CSV to a temp file and return the path."""
    output_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    path = output_file.name

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)

        for i, entity in enumerate(state.entities, start=1):
            writer.writerow([
                i,
                entity.entity_name_cleaned,
                entity.final_decision_maker or "Not Found",
                entity.final_parent_company or "",
                entity.final_website or "",
                entity.final_job_title or "",
                entity.final_linkedin or "",
                entity.final_email or "",
                entity.final_phone or "",
                entity.final_confidence or "",
            ])

    await emit_log(state, "INFO", f"Output CSV written: {len(state.entities)} rows")
    return path
```

## Main App Wiring

```python
# backend/app/main.py — MODIFY existing file
# Replace the contents with this (keeps /health):
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.state import AppState, init_quotas
from app.core.logging import configure_logging
from app.routers import health, run, events, download


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.pipeline = AppState()
    init_quotas(app.state.pipeline)
    yield
    # On shutdown: cancel any in-flight orchestrator
    state = app.state.pipeline
    if state.current_task and not state.current_task.done():
        state.current_task.cancel()


app = FastAPI(title="LeadFinder", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(run.router)
app.include_router(events.router)
app.include_router(download.router)
```

## Tests

### backend/tests/test_orchestrator.py

Unit tests on the orchestrator using stubs (no HTTP). Verify it:

1. Processes all entities to terminal state
2. Increments counters correctly
3. Marks stub-processed entities as `unenriched` (no sos/contacts)
4. Cancellation is respected between entities
5. Stage 5 output file is written with correct row count

```python
# backend/tests/test_orchestrator.py — NEW FILE
import asyncio
import pytest
from pathlib import Path
from datetime import datetime, timezone
from app.state import AppState, init_quotas
from app.models.run import RunState
from app.models.entity import CleanedEntity, EntityType, MailingAddress, SourceParcel
from app.pipeline.orchestrator import orchestrate


def _make_entity(name: str) -> CleanedEntity:
    return CleanedEntity(
        entity_name_raw=name,
        entity_name_cleaned=name,
        entity_name_normalized=name.upper(),
        entity_type=EntityType.LLC,
        mailing_address=MailingAddress(state="TX"),
        source_parcels=[SourceParcel(apn="APN-1", property_state="TX")],
    )


@pytest.fixture
def fresh_state():
    s = AppState()
    init_quotas(s)
    s.current_run = RunState(
        run_id="test123",
        status="running",
        started_at=datetime.now(timezone.utc),
        current_stage="parsing",
        entities_total=3,
    )
    s.entities = [_make_entity(f"ACME {i} LLC") for i in range(3)]
    return s


@pytest.mark.asyncio
async def test_orchestrator_processes_all_entities(fresh_state):
    await orchestrate(fresh_state)
    assert fresh_state.current_run.status == "completed"
    assert fresh_state.current_run.entities_processed == 3
    # Stubs produce no sos/contacts, so all go to unenriched
    assert fresh_state.current_run.entities_unenriched == 3


@pytest.mark.asyncio
async def test_orchestrator_writes_output(fresh_state):
    await orchestrate(fresh_state)
    assert fresh_state.current_run.output_path is not None
    assert Path(fresh_state.current_run.output_path).exists()
    content = Path(fresh_state.current_run.output_path).read_text(encoding="utf-8")
    # Header + 3 data rows
    assert content.count("\n") >= 3
    assert "ACME 0 LLC" in content
    assert "Not Found" in content


@pytest.mark.asyncio
async def test_orchestrator_respects_cancellation(fresh_state):
    # Flip status to cancelled before orchestrator reaches processing stage
    async def cancel_after_start():
        await asyncio.sleep(0.01)
        fresh_state.current_run.status = "cancelled"

    await asyncio.gather(
        orchestrate(fresh_state),
        cancel_after_start(),
    )
    # Whatever state orchestrator leaves: either it bailed (cancelled) or finished super fast.
    # Either way it must NOT be "failed" and status is one of these two.
    assert fresh_state.current_run.status in ("cancelled", "completed")
```

### backend/tests/test_stage_1_run.py

HTTP integration tests using TestClient. Verify:

1. POST /api/run with valid CSV → 202 with cleaning_summary
2. POST /api/run twice concurrently → second gets 409
3. POST /api/run with non-CSV → 400
4. GET /api/events returns text/event-stream content type
5. GET /api/download before any run → 404
6. DELETE /api/run when no run active → returns "no_active_run"
7. Full end-to-end: POST a small CSV, await completion, GET /api/download returns CSV

```python
# backend/tests/test_stage_1_run.py — NEW FILE
import asyncio
import io
import pytest
from fastapi.testclient import TestClient
from app.main import app


# Use the existing Stage 0 fixture CSV (14 rows, keeps 4 entities).
FIXTURE_CSV_PATH = "tests/fixtures/sample_input.csv"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_still_works(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_post_run_with_valid_csv_returns_202(client):
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r = client.post("/api/run", files=files)

    assert r.status_code == 200  # FastAPI default — we return 200 with run payload
    body = r.json()
    assert body["status"] == "running"
    assert "run_id" in body
    assert body["cleaning_summary"]["input_rows"] == 14


def test_post_run_with_non_csv_returns_400(client):
    files = {"file": ("foo.txt", io.BytesIO(b"not a csv"), "text/plain")}
    r = client.post("/api/run", files=files)
    assert r.status_code == 400


def test_download_before_run_returns_404(client):
    r = client.get("/api/download")
    assert r.status_code == 404


def test_delete_when_no_active_run(client):
    r = client.delete("/api/run")
    assert r.status_code == 200
    assert r.json()["status"] == "no_active_run"


def test_events_endpoint_returns_sse_content_type(client):
    # Note: streaming — we just check the content type on the first chunk.
    with client.stream("GET", "/api/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")


def test_end_to_end_run_and_download(client):
    """Full lifecycle: upload CSV → wait for completion → download output."""
    # Run
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r = client.post("/api/run", files=files)
    assert r.status_code == 200

    # Wait for run to finish (stubs are fast; 5 sec max)
    import time
    start = time.time()
    while time.time() - start < 10:
        # Poll via DELETE-style check: the GET /api/download will 404 until done
        r2 = client.get("/api/download")
        if r2.status_code == 200:
            break
        time.sleep(0.2)
    else:
        pytest.fail("Run did not complete within 10 seconds")

    # Download
    r3 = client.get("/api/download")
    assert r3.status_code == 200
    assert r3.headers["content-type"].startswith("text/csv")
    text = r3.text
    assert "LLC Company" in text  # header
    # 4 data rows (Stage 0 keeps 4 entities from the 14-row fixture)
    assert text.count("\n") >= 4


def test_concurrent_post_returns_409(client):
    """After a run is started, a second POST should get 409."""
    # First run (don't wait for completion)
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r1 = client.post("/api/run", files=files)
    assert r1.status_code == 200

    # Immediately try again
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r2 = client.post("/api/run", files=files)
    # Might be 409 if still running, or 200 if stubs finished instantly.
    # On Windows, TestClient tends to run the async task fast enough that this
    # is a race. We accept either but assert the error shape when it's 409.
    assert r2.status_code in (200, 409)
    if r2.status_code == 409:
        detail = r2.json()["detail"]
        assert detail["error"] == "run_in_progress"
        assert "current_run" in detail
```

## Config Update

Verify `backend/app/core/config.py` has `max_parallel_entities: int = 8`. If not already there, add it. (Existing Stage 0 spec documents this field.)

## pyproject.toml

No new runtime dependencies. FastAPI, pydantic, structlog, python-multipart are already there. If `pytest-asyncio` is missing from dev deps, add it (orchestrator tests need it).

Add to `[tool.pytest.ini_options]` in pyproject.toml if not present:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Acceptance Criteria

After you finish:

1. `cd backend && python -m pytest -v` — ALL tests pass. Expected count: **23 + ~10 new = ~33 tests passing** (Stage 0: 10, Stage 2: 13, Stage 1: ~10).

2. `uvicorn app.main:app --reload` starts without errors.

3. `curl http://localhost:8000/health` → `{"status":"ok","version":"0.1.0"}`

4. Manual smoke test:

```
   curl -F "file=@tests/fixtures/sample_input.csv" http://localhost:8000/api/run
   # → 200 with cleaning_summary
   # Within a couple seconds:
   curl http://localhost:8000/api/download -o out.csv
   # → 200; out.csv has header + 4 rows
```

## What NOT to Do

- DO NOT touch stage_0_cleaning.py or stage_2_parsing.py logic
- DO NOT touch the existing Stage 0 or Stage 2 test files
- DO NOT modify health.py router
- DO NOT add a database, queue, or cloud storage
- DO NOT add auth/login
- DO NOT implement real Stage 3 or Stage 4 logic — they are stubs in this prompt
- DO NOT add confidence scoring logic to Stage 5 — that comes in a later prompt
- DO NOT add persistence of runs across server restart
- DO NOT add any new runtime dependencies beyond python-multipart (already present)

## Before You Code

ASK clarifying questions if ANY rule is ambiguous. Likely questions and my preferred answers:

- **"Should `test_concurrent_post_returns_409` be reliable?"**
  → It's inherently racy because the stub pipeline is fast. Accept either 200 or 409 and only assert the error shape when 409. (See test comment above.)

- **"What if a user uploads a 0-byte CSV?"**
  → Stage 0's `clean_csv` will raise ValueError → we return 400. No special handling needed.

- **"Should SSE backfill include all historical log lines or only current run?"**
  → Only the rolling buffer (maxlen=500 events). This is already handled by `log_buffer.clear()` in `reset_for_new_run()`.

- **"Does the orchestrator retry failed entities?"**
  → Not in Stage 1. An exception in stage processing marks the entity `failed` and moves on. Retry logic belongs in Stage 3/4 prompts.

- **"What if the SSE subscriber queue fills up?"**
  → Drop the event for that slow subscriber (already handled via `QueueFull` catch). Don't block the broadcaster.

- **"Should I add CORS middleware?"**
  → Yes, for dev. Allow `http://localhost:3000` (Next.js dev server). Add `CORSMiddleware` in main.py with that single origin.

Show me your plan (list of files + one-line description of each) and wait for my explicit "approved" before writing code. If ANY spec detail is unclear or self-contradictory, ASK first.
