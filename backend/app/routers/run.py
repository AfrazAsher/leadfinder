import asyncio
import tempfile
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.events import emit_log
from app.models.run import RunProgressSnapshot, RunState
from app.pipeline.orchestrator import orchestrate
from app.pipeline.stage_0_cleaning import clean_csv
from app.state import AppState

router = APIRouter()


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


@router.post("/api/run")
async def start_run(request: Request, file: UploadFile = File(...)):
    state: AppState = request.app.state.pipeline

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

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False)
    tmp.close()
    upload_path = tmp.name
    try:
        with open(upload_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to save upload: {e}")

    try:
        report = clean_csv(upload_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"CSV validation failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleaning failed: {e}")

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
    state.entities = list(report.entities)

    state.current_task = asyncio.create_task(orchestrate(state))

    await emit_log(state, "INFO", f"Run {run_id} started with {len(report.entities)} entities")

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
