from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class RunState(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    current_stage: str
    entities_total: int = 0
    entities_processed: int = 0
    entities_resolved: int = 0
    entities_unenriched: int = 0
    entities_failed: int = 0
    cleaning_summary: Optional[dict] = None
    output_path: Optional[str] = None
    error_message: Optional[str] = None


class RunProgressSnapshot(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    current_stage: str
    entities_total: int
    entities_processed: int
    elapsed_seconds: float
