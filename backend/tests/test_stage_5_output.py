import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.entity import CleanedEntity, EntityType, MailingAddress, SourceParcel
from app.models.run import RunState
from app.pipeline.stage_5_output import audit_path_for, generate_output
from app.state import AppState, init_quotas


def _make_state_with_run() -> AppState:
    s = AppState()
    init_quotas(s)
    s.current_run = RunState(
        run_id="test123",
        status="running",
        started_at=datetime.now(timezone.utc),
        current_stage="output",
        entities_total=0,
    )
    return s


def _make_unenriched_entity() -> CleanedEntity:
    e = CleanedEntity(
        entity_name_raw="UNFOUND LLC",
        entity_name_cleaned="UNFOUND LLC",
        entity_name_normalized="UNFOUND LLC",
        entity_name_search="UNFOUND LLC",
        entity_type=EntityType.LLC,
        mailing_address=MailingAddress(state="TX"),
        source_parcels=[SourceParcel(apn="A", property_state="TX")],
        filing_state_candidates=["TX"],
    )
    e.status = "unenriched"
    return e


def _make_resolved_entity_with_officers(
    officers: list[dict],
    status: str = "Active",
    name: str = "TEST LLC",
) -> CleanedEntity:
    e = CleanedEntity(
        entity_name_raw=name,
        entity_name_cleaned=name,
        entity_name_normalized=name.upper(),
        entity_name_search=name.upper(),
        entity_type=EntityType.LLC,
        mailing_address=MailingAddress(state="FL"),
        source_parcels=[SourceParcel(apn="A", property_state="FL")],
        filing_state_candidates=["FL"],
    )
    e.sos_source = "fl_direct"
    e.sos_results = [{
        "filing_number": "L123",
        "entity_name": name,
        "status": status,
        "principal_address": {"street": "1 Main", "city": "Miami", "state": "FL", "zip": "33101"},
        "mailing_address": None,
        "registered_agent": None,
        "officers": officers,
        "filing_date": "01/01/2020",
        "source_url": None,
    }]
    e.status = "resolved"
    return e


@pytest.mark.asyncio
async def test_stage_5_writes_real_decision_maker():
    state = _make_state_with_run()
    state.entities = [_make_resolved_entity_with_officers([
        {"name": "Asst Sec A", "title": "Assistant Secretary",
         "address": {"street": "1 A"}},
        {"name": "Walt Disney Attractions Trust", "title": "Sole Member",
         "address": {"street": "1 B"}},
    ])]
    path = await generate_output(state)
    content = Path(path).read_text(encoding="utf-8")
    assert "Walt Disney Attractions Trust" in content
    assert "Sole Member" in content
    assert "HIGH" in content or "MEDIUM-HIGH" in content


@pytest.mark.asyncio
async def test_stage_5_writes_not_found_for_unenriched():
    state = _make_state_with_run()
    state.entities = [_make_unenriched_entity()]
    path = await generate_output(state)
    content = Path(path).read_text(encoding="utf-8")
    assert "Not Found" in content


@pytest.mark.asyncio
async def test_stage_5_writes_audit_json_alongside_csv():
    state = _make_state_with_run()
    state.entities = [_make_resolved_entity_with_officers([
        {"name": "Jane", "title": "Manager", "address": {"street": "1 Main"}},
    ])]
    csv_path = await generate_output(state)
    audit_path = audit_path_for(csv_path)
    assert audit_path.exists()
    data = json.loads(audit_path.read_text(encoding="utf-8"))
    assert data["total_entities"] == 1
    assert data["entities"][0]["selection"]["chosen_officer"]["name"] == "Jane"


@pytest.mark.asyncio
async def test_stage_5_dissolved_entity_tier_reflects_status():
    state = _make_state_with_run()
    entity = _make_resolved_entity_with_officers(
        [{"name": "X", "title": "Manager", "address": {"street": "1 Main"}}],
        status="Dissolved",
    )
    state.entities = [entity]
    await generate_output(state)
    assert entity.final_tier in ("LOW", "blank")


@pytest.mark.asyncio
async def test_stage_5_csv_has_10_columns():
    state = _make_state_with_run()
    state.entities = [_make_resolved_entity_with_officers(
        [{"name": "Jane", "title": "Manager", "address": {"street": "1 Main"}}]
    )]
    path = await generate_output(state)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows[0]) == 10
    assert len(rows[1]) == 10
