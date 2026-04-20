from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.pipeline.stage_5_output import audit_path_for
from app.state import AppState

router = APIRouter()


@router.get("/api/download")
async def download(
    request: Request,
    format: Literal["csv", "audit"] = Query("csv"),
):
    state: AppState = request.app.state.pipeline

    if not state.current_run:
        raise HTTPException(status_code=404, detail="No run has been started yet")

    if state.current_run.status != "completed":
        raise HTTPException(
            status_code=404,
            detail=f"Output not available; run status is '{state.current_run.status}'",
        )

    output_path = state.current_run.output_path
    if not output_path:
        raise HTTPException(status_code=500, detail="Output file is missing")

    if format == "audit":
        file_path = audit_path_for(output_path)
        media_type = "application/json"
        filename = f"leadfinder_enriched_{state.current_run.run_id}.audit.json"
    else:
        file_path = Path(output_path)
        media_type = "text/csv"
        filename = f"leadfinder_enriched_{state.current_run.run_id}.csv"

    if not file_path.exists():
        raise HTTPException(status_code=500, detail="Output file is missing")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
    )
