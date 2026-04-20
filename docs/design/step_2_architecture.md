# Step 2: System Architecture

## 2.1 Scope

This document defines the tech stack, components, and how they connect for
LeadFinder as a **single-user local research tool**. It covers what's built,
what's deferred, and the tradeoffs made.

## 2.2 System Overview

```
┌──────────────────────────────────────────────────────┐
│  BROWSER (single page)                               │
│  ┌────────────────────────────────────────────────┐  │
│  │  Next.js + shadcn/ui                           │  │
│  │                                                │  │
│  │  - Upload CSV (dropzone)                       │  │
│  │  - Start Pipeline button                       │  │
│  │  - Live stats (4 cards)                        │  │
│  │  - 5 pipeline-stage cards                      │  │
│  │  - Live log window (SSE stream)                │  │
│  │  - Enrichment coverage bars                    │  │
│  │  - Data sources list                           │  │
│  │  - Sample output preview                       │  │
│  │  - Download button (enabled when done)         │  │
│  └───────────────────┬────────────────────────────┘  │
└──────────────────────┼───────────────────────────────┘
                       │ HTTP
                       │ (3 endpoints)
                       ▼
┌──────────────────────────────────────────────────────┐
│  FastAPI PROCESS (single)                            │
│                                                      │
│  Routes:                                             │
│    POST /api/run       (multipart CSV upload)        │
│    GET  /api/events    (SSE log stream)              │
│    GET  /api/download  (enriched CSV)                │
│    GET  /health                                      │
│                                                      │
│  In-memory state (app.state):                        │
│    - current_run: dict | None                        │
│    - log_buffer: deque(maxlen=500)                   │
│    - sse_subscribers: set[asyncio.Queue]             │
│    - entities: list[CleanedEntity]                   │
│    - cache: dict[str, Any]                           │
│    - quotas: dict[str, int]                          │
│    - output_path: str | None (temp file)             │
│                                                      │
│  Pipeline modules (pure Python):                     │
│    stage_0_cleaning.py    ✓ shipped                  │
│    stage_2_parsing.py                                │
│    stage_3_sos.py                                    │
│    stage_4_enrichment.py                             │
│    stage_5_output.py                                 │
│    orchestrator.py                                   │
│                                                      │
│  Providers (behind interface):                       │
│    OpenCorporates, Serper, Hunter, Apollo            │
│    SOS scrapers (Playwright): FL, NC, WA, UT         │
│    Stubs: ZoomInfo, RocketReach, TX direct           │
└──────────────────────────────────────────────────────┘
```

One process. One user. One pipeline run at a time.

## 2.3 Tech Stack (Locked)

| Layer              | Choice                         | Rationale                                       |
| ------------------ | ------------------------------ | ----------------------------------------------- |
| Backend language   | Python 3.10+                   | pipeline logic, scraping, async I/O             |
| Web framework      | FastAPI                        | async-native, Pydantic integration, SSE support |
| Browser automation | Playwright                     | best Cloudflare resistance on free tooling      |
| HTTP client        | httpx                          | async-native                                    |
| Frontend framework | Next.js 14 (App Router)        | shadcn/ui compatibility                         |
| Styling            | Tailwind + shadcn/ui           | copy-paste components, no dep hell              |
| Config             | pydantic-settings v2           | typed env, `.env` support                       |
| Logging            | structlog                      | JSON in prod, colored console in dev            |
| Testing            | pytest + pytest-asyncio        | already used by Stage 0                         |
| Linting            | ruff                           | fast, single tool                               |
| Packaging          | pip + pyproject.toml           | simple, no lockfile gymnastics                  |
| Container          | Docker (Playwright base image) | client runs it on their own infra               |

## 2.4 Component Responsibilities

### Frontend (Next.js, single page)

One page (`app/page.tsx`). Client-side state only. No routing.

**What it does:**

- Drag-drop CSV upload with client-side validation
- POST to `/api/run` with the file
- Open `EventSource` to `/api/events` — appends log lines to the log window,
  updates stats + stage cards as events arrive
- When done event received, enable Download button pointing to
  `/api/download`

**What it does not:**

- Manage runs
- Poll for status (SSE instead)
- Persist anything
- Authenticate

### Backend (FastAPI)

**3 routes:**

```
POST /api/run
  Body: multipart form with CSV file
  Behavior:
    - If another run is in progress: 409 Conflict with progress info
    - Save CSV to temp file
    - Run Stage 0 synchronously (1-2 sec for 341 rows)
      - On failure: 400 Bad Request with specific reason
    - Initialize app.state.current_run
    - asyncio.create_task() for Stages 2-5
    - Return 202 Accepted with cleaning summary
  Response:
    {
      "run_id": "abc-123",
      "status": "running",
      "cleaning_summary": {
        "input_rows": 341,
        "entities_kept": 95,
        "skipped": {...}
      }
    }

GET /api/events
  Response: text/event-stream (Server-Sent Events)
  Behavior:
    - Backend pushes:
      - "log" events (single log line)
      - "stats" events (stage progress, entity counts)
      - "stage" events (stage transitions with check marks)
      - "done" event (pipeline finished; output ready)
    - Closes on pipeline completion or error

GET /api/download
  Response: text/csv (streamed)
  Behavior:
    - 404 if no output available
    - Reads from app.state.output_path and returns as attachment
    - Filename: enriched_<timestamp>.csv
```

**Orchestrator:** glues Stages 2–5 together, respects concurrency limit
(8 entities at once via semaphore), writes events to SSE subscribers, handles
errors gracefully.

**Providers:** each external source implements a common Protocol. New
providers plug in by implementing the interface. Paid-API providers start as
stubs returning empty results with a clear log line; client plugs in their
API key to activate real behavior.

### In-memory state

All transient. Lives in `app.state`. Gone on process restart.

| Key               | Type                              | Purpose                                              |
| ----------------- | --------------------------------- | ---------------------------------------------------- |
| `current_run`     | dict or None                      | Status snapshot; `None` means idle                   |
| `log_buffer`      | `deque(maxlen=500)`               | Recent log lines (late SSE subscribers get backfill) |
| `sse_subscribers` | `set[asyncio.Queue]`              | Active SSE connections                               |
| `entities`        | `list[CleanedEntity]`             | Current run's in-flight entity records               |
| `cache`           | `dict[str, tuple[Any, datetime]]` | Response cache with TTLs                             |
| `quotas`          | `dict[str, int]`                  | Per-source call counters                             |
| `output_path`     | str or None                       | Path to generated CSV                                |
| `current_task`    | asyncio.Task                      | For cancellation on `DELETE /api/run`                |

## 2.5 Concurrency Rules

**One pipeline run at a time.**

- `POST /api/run` while a run is running → `409 Conflict` with:

  ```json
  {
    "error": "run_in_progress",
    "message": "A pipeline run is currently in progress (started 3 min ago, ~12 min remaining). Please wait for it to finish before starting a new run.",
    "current_run": {
      "started_at": "2026-04-20T14:35:00Z",
      "current_stage": "contact_enrichment",
      "progress": "47 of 95 entities processed"
    }
  }
  ```

- UI catches 409, shows banner "System is busy", displays the current_run
  info, periodically re-tries POST.

**Inside a run, 8 entities process concurrently** via `asyncio.Semaphore(8)`.
(Tunable via `MAX_PARALLEL_ENTITIES` env var.)

## 2.6 Deployment Topology

### Development

```
Dev laptop
├── backend:     `uvicorn app.main:app --reload` (port 8000)
└── frontend:    `npm run dev` (port 3000)
```

Frontend dev proxies `/api/*` requests to `localhost:8000`.

### Production (how the client runs it)

```
Client's VM or workstation
└── Docker container (single)
    ├── FastAPI serving /api/* on 8000
    └── Next.js static export served by FastAPI on /
```

Single container. One `docker run` command.
Environment variables passed via `.env` or Docker env flags.

### Railway (optional)

If client wants a hosted version later: same Dockerfile, push to Railway,
one service. No changes needed. Not required for MVP.

## 2.7 What About Persistence?

Nothing persists across process restart. Specifically:

- **Completed run's output CSV** — lives in a temp file. If the process
  restarts, it's gone. User re-uploads.
- **Cache entries** — in-memory dict; gone on restart. Every first run pays
  full API cost.
- **Quota counters** — in-memory; reset to 0 on restart (which is actually
  wrong for free tiers but acceptable for demo — see §2.9).
- **Logs** — recent buffer in memory; historical logs not retained.

**Why accept this:** the tool is intended for single-user interactive
sessions. The reviewer stays present during the run. If the process crashes,
they retry. No mission-critical persistence.

**Phase 2 note:** if the client asks for "I want to come back tomorrow and
download yesterday's run" — that's when we add SQLite or a file-backed cache.
Still no full DB.

## 2.8 What About Multi-User?

Explicitly not supported. If two users access the tool simultaneously:

- First to POST wins; second gets 409
- Log stream is global; both users see the same logs
- Output is global; whoever downloads last wins

If the client later says "we need per-user runs," that's a Phase 2 conversation.
It means adding: run_ids in URLs, per-run log channels, per-run output files,
cleanup policy, and basic multi-run UI state. Not trivial. Not scoped in v1.

## 2.9 Known Tradeoffs

**Quota counter reset on restart is wrong.** If the user runs once (uses 80
of 100 Google PSE calls), restarts the server, and runs again (uses another
80), we've actually used 160 but the counter says 80. Google will throttle us.

Acceptable because: (a) restart is rare in normal use, (b) quota exhaustion
degrades gracefully (source silently skipped), (c) demo environment; in
production the client's paid-tier quotas are far larger.

**No persistence for the demo is a feature, not a bug.** The client told us
to keep it simple. Every piece of state we add is a failure mode we have to
handle.

**Playwright in Docker needs the specific base image.** Using a bare
Python image won't work — missing Chromium system libraries. Base image:
`mcr.microsoft.com/playwright/python:v1.44.0-jammy`.

**SSE reconnection not handled.** If the browser loses connection mid-run,
it won't auto-reconnect and replay missed logs. Acceptable for local use;
if the client ever hosts this over the public internet, add reconnect +
Last-Event-ID replay.

## 2.10 What Step 2 Leaves to Later Steps

- Entity flow through the pipeline → Step 3 (Pipeline Design)
- Confidence scoring formula → Step 4
- Entity matching / disambiguation → Step 5
- Exact folder structure + tests → Step 6 (Implementation Plan)
