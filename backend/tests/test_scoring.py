from app.models.entity import CleanedEntity, EntityType, MailingAddress, SourceParcel
from app.pipeline.scoring import entity_identity_score, tier_for_score


def _make_entity_with_sos(
    source: str,
    status: str,
    filing_number: str = "L123",
    principal_present: bool = True,
) -> CleanedEntity:
    entity = CleanedEntity(
        entity_name_raw="X LLC",
        entity_name_cleaned="X LLC",
        entity_name_normalized="X LLC",
        entity_name_search="X LLC",
        entity_type=EntityType.LLC,
        mailing_address=MailingAddress(state="FL"),
        source_parcels=[SourceParcel(apn="A", property_state="FL")],
        filing_state_candidates=["FL"],
    )
    principal_addr = {"street": "1 Main", "city": "Miami", "state": "FL", "zip": "33101"}
    entity.sos_source = source
    entity.sos_results = [{
        "filing_number": filing_number,
        "entity_name": "X LLC",
        "status": status,
        "principal_address": principal_addr if principal_present else None,
        "mailing_address": None,
        "registered_agent": None,
        "officers": [],
        "filing_date": None,
        "source_url": None,
    }]
    entity.status = "resolved"
    return entity


def test_tier_high_boundary():
    assert tier_for_score(0.85) == "HIGH"
    assert tier_for_score(0.849999) == "MEDIUM-HIGH"


def test_tier_all_boundaries():
    assert tier_for_score(0.70) == "MEDIUM-HIGH"
    assert tier_for_score(0.55) == "MEDIUM"
    assert tier_for_score(0.40) == "LOW"
    assert tier_for_score(0.399) == "blank"


def test_active_fl_with_manager_scores_high():
    entity = _make_entity_with_sos(
        "fl_direct", "Active", filing_number="L123", principal_present=True
    )
    officer = {"name": "X", "title": "Manager", "address": {"street": "1 Main"}}
    score = entity_identity_score(entity, officer)
    assert score >= 0.85


def test_dissolved_entity_scores_low():
    entity = _make_entity_with_sos(
        "fl_direct", "Dissolved", filing_number="L123", principal_present=True
    )
    officer = {"name": "X", "title": "Manager", "address": {"street": "1 Main"}}
    score = entity_identity_score(entity, officer)
    assert score < 0.5


def test_missing_address_reduces_completeness():
    sparse = _make_entity_with_sos(
        "fl_direct", "Active", filing_number="L123", principal_present=False
    )
    sparse_officer = {"name": "X", "title": "Manager", "address": None}
    full = _make_entity_with_sos(
        "fl_direct", "Active", filing_number="L123", principal_present=True
    )
    full_officer = {"name": "X", "title": "Manager", "address": {"street": "1 Main"}}
    assert entity_identity_score(sparse, sparse_officer) < entity_identity_score(
        full, full_officer
    )


def test_exotic_title_reduces_score():
    entity = _make_entity_with_sos(
        "fl_direct", "Active", filing_number="L123", principal_present=True
    )
    with_manager = {"name": "X", "title": "Manager", "address": {"street": "1"}}
    with_exotic = {"name": "X", "title": "Chief Revenue Wizard", "address": {"street": "1"}}
    assert entity_identity_score(entity, with_manager) > entity_identity_score(
        entity, with_exotic
    )


def test_score_clamped_to_01_range():
    entity = _make_entity_with_sos(
        "fl_direct", "Active", filing_number="L123", principal_present=True
    )
    officer = {"name": "X", "title": "Sole Member", "address": {"street": "1"}}
    s = entity_identity_score(entity, officer)
    assert 0.0 <= s <= 1.0
