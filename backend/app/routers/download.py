from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
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
