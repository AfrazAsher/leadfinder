from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entity import CleanedEntity, EntityType, MailingAddress, SourceParcel
from app.models.run import RunState
from app.pipeline.stage_3_sos import lookup_sos
from app.providers.base import SOSResult
from app.state import AppState, init_quotas


def _make_entity(filing_states: list[str]):
    return CleanedEntity(
        entity_name_raw="TEST LLC",
        entity_name_cleaned="TEST LLC",
        entity_name_normalized="TEST LLC",
        entity_name_search="TEST LLC",
        search_name_variants=["TEST LLC", "TEST"],
        entity_type=EntityType.LLC,
        mailing_address=MailingAddress(state=filing_states[0]),
        source_parcels=[SourceParcel(apn="APN-1", property_state=filing_states[0])],
        filing_state_candidates=filing_states,
    )


def _make_state():
    s = AppState()
    init_quotas(s)
    s.current_run = RunState(
        run_id="test123",
        status="running",
        started_at=datetime.now(timezone.utc),
        current_stage="sos_lookup",
        entities_total=1,
    )
    return s


@pytest.mark.asyncio
async def test_lookup_sos_falls_forward_when_first_state_empty(monkeypatch):
    state = _make_state()
    browser = MagicMock()
    state.cache["__browser__"] = browser
    entity = _make_entity(["FL", "NC"])

    fl_provider = MagicMock(state_code="FL", display_name="FL")
    fl_provider.search = AsyncMock(return_value=None)
    nc_provider = MagicMock(state_code="NC", display_name="NC")
    nc_provider.search = AsyncMock(return_value=SOSResult(
        filing_number="X",
        entity_name="TEST LLC",
        status="Active",
        officers=[{"name": "JANE DOE", "title": "Manager"}],
    ))

    def fake_build(s, b):
        return {"FL": fl_provider, "NC": nc_provider}

    monkeypatch.setattr("app.pipeline.stage_3_sos.build_providers", fake_build)

    await lookup_sos(state, entity)
    assert entity.sos_source == "nc_direct"
    assert len(entity.sos_results) == 1
    assert entity.sos_results[0]["entity_name"] == "TEST LLC"


@pytest.mark.asyncio
async def test_lookup_sos_empty_when_no_states_match(monkeypatch):
    state = _make_state()
    state.cache["__browser__"] = MagicMock()
    entity = _make_entity(["FL", "NC"])

    fl_provider = MagicMock(state_code="FL")
    fl_provider.search = AsyncMock(return_value=None)
    nc_provider = MagicMock(state_code="NC")
    nc_provider.search = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.pipeline.stage_3_sos.build_providers",
        lambda s, b: {"FL": fl_provider, "NC": nc_provider},
    )

    await lookup_sos(state, entity)
    assert entity.sos_source is None
    assert entity.sos_results == []


@pytest.mark.asyncio
async def test_lookup_sos_skips_states_without_scraper(monkeypatch):
    state = _make_state()
    state.cache["__browser__"] = MagicMock()
    entity = _make_entity(["TX", "DE"])

    monkeypatch.setattr(
        "app.pipeline.stage_3_sos.build_providers",
        lambda s, b: {},
    )

    await lookup_sos(state, entity)
    assert entity.sos_results == []
    assert entity.sos_source is None
