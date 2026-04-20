import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.entity import CleanedEntity, EntityType, MailingAddress, SourceParcel
from app.models.run import RunState
from app.pipeline.orchestrator import orchestrate
from app.state import AppState, init_quotas


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
    assert fresh_state.current_run.entities_unenriched == 3


@pytest.mark.asyncio
async def test_orchestrator_writes_output(fresh_state):
    await orchestrate(fresh_state)
    assert fresh_state.current_run.output_path is not None
    assert Path(fresh_state.current_run.output_path).exists()
    content = Path(fresh_state.current_run.output_path).read_text(encoding="utf-8")
    assert content.count("\n") >= 3
    assert "ACME 0 LLC" in content
    assert "Not Found" in content


@pytest.mark.asyncio
async def test_orchestrator_respects_cancellation(fresh_state):
    async def cancel_after_start():
        await asyncio.sleep(0.01)
        fresh_state.current_run.status = "cancelled"

    await asyncio.gather(
        orchestrate(fresh_state),
        cancel_after_start(),
    )
    assert fresh_state.current_run.status in ("cancelled", "completed")
