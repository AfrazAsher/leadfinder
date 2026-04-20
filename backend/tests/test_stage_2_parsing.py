from app.models.entity import (
    CleanedEntity,
    EntityType,
    MailingAddress,
    SourceParcel,
)
from app.pipeline.stage_2_parsing import parse_entity
from tests.fixtures.stage_2_cases import CASES


def make_entity(
    name: str,
    entity_type: EntityType = EntityType.LLC,
    mailing_state: str = "TX",
    property_states: list[str] = None,
) -> CleanedEntity:
    if property_states is None:
        property_states = [mailing_state]

    return CleanedEntity(
        entity_name_raw=name,
        entity_name_cleaned=name,
        entity_name_normalized=name.upper().strip(),
        entity_type=entity_type,
        mailing_address=MailingAddress(
            street="123 MAIN ST",
            city="CITY",
            state=mailing_state,
            zip="00000",
            complete=True,
        ),
        source_parcels=[
            SourceParcel(apn=f"APN-{i}", property_state=ps)
            for i, ps in enumerate(property_states)
        ],
    )


def _make_from_case(key: str) -> CleanedEntity:
    c = CASES[key]
    return make_entity(
        name=c["name"],
        entity_type=c["entity_type"],
        mailing_state=c["mailing_state"],
        property_states=c["property_states"],
    )


def test_simple_llc_tx():
    e = _make_from_case("simple_llc_tx")
    parse_entity(e)
    assert e.entity_name_search == "ROLATOR & INDEPENDENCE LLC"
    assert e.search_name_variants[0] == e.entity_name_search
    assert "ROLATOR AND INDEPENDENCE LLC" in e.search_name_variants
    assert "ROLATOR & INDEPENDENCE" in e.search_name_variants
    assert e.filing_state_candidates == ["TX", "DE"]


def test_multi_state_md_nc():
    e = _make_from_case("multi_state_md_nc")
    parse_entity(e)
    assert e.filing_state_candidates == ["MD", "NC", "DE"]


def test_delaware_domiciled():
    e = _make_from_case("delaware_domiciled")
    parse_entity(e)
    assert e.filing_state_candidates == ["DE"]
    assert e.filing_state_candidates.count("DE") == 1


def test_trust_with_boilerplate():
    e = _make_from_case("trust_with_boilerplate")
    parse_entity(e)
    assert e.entity_name_search == "FIELDS FAMILY TRUST"
    assert e.filing_state_candidates == []
    # Pre-strip form retained as a variant
    assert any("U/A DTD" in v for v in e.search_name_variants)
    assert e.search_name_variants[0] == e.entity_name_search


def test_trailing_the():
    e = _make_from_case("trailing_the")
    parse_entity(e)
    assert e.entity_name_search == "WAYZATA TRUST"
    assert e.filing_state_candidates == []


def test_slash_name():
    e = _make_from_case("slash_name")
    parse_entity(e)
    assert "STOCKTON 65TH L P" in e.search_name_variants
    assert "STOCKTON AND 65TH L P" in e.search_name_variants
    assert e.filing_state_candidates == ["CA", "DE"]
    assert e.search_name_variants[0] == e.entity_name_search


def test_and_present():
    e = _make_from_case("and_present")
    parse_entity(e)
    assert "SMITH & JONES LLC" in e.search_name_variants
    assert e.search_name_variants[0] == e.entity_name_search


def test_charitable_remainder():
    e = _make_from_case("charitable_remainder")
    parse_entity(e)
    assert "CHARITABLE REMAINDER" not in e.entity_name_search
    assert e.entity_name_search.startswith("COOK INVESTMENTS")
    assert e.filing_state_candidates == []


def test_suffix_stripping():
    e = _make_from_case("suffix_stripping")
    parse_entity(e)
    assert "JEFFERSON OWNSBY" in e.search_name_variants
    assert e.search_name_variants[0] == "JEFFERSON OWNSBY LLC"


def test_no_property_state():
    e = _make_from_case("no_property_state")
    parse_entity(e)
    assert e.filing_state_candidates == ["TX", "DE"]


def test_dedupe_minimal():
    e = _make_from_case("dedupe_minimal")
    parse_entity(e)
    assert e.search_name_variants == ["ACME LLC", "ACME"]


def test_max_variants():
    e = _make_from_case("max_variants")
    parse_entity(e)
    assert len(e.search_name_variants) <= 5
    assert e.search_name_variants[0] == e.entity_name_search


def test_us_territory_falls_through_to_delaware():
    e = _make_from_case("us_territory")
    parse_entity(e)
    assert e.filing_state_candidates == ["DE"]
