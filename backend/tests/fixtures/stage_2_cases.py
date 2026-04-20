from app.models.entity import EntityType

CASES: dict[str, dict] = {
    "simple_llc_tx": {
        "name": "ROLATOR & INDEPENDENCE LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "TX",
        "property_states": ["TX"],
    },
    "multi_state_md_nc": {
        "name": "ROBERTSON CROSSING LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "MD",
        "property_states": ["NC"],
    },
    "delaware_domiciled": {
        "name": "FOO LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "DE",
        "property_states": ["DE"],
    },
    "trust_with_boilerplate": {
        "name": "FIELDS FAMILY TRUST THE U/A DTD SEPTEMBER 10, 2018",
        "entity_type": EntityType.TRUST,
        "mailing_state": "MN",
        "property_states": ["MN"],
    },
    "trailing_the": {
        "name": "WAYZATA TRUST THE",
        "entity_type": EntityType.TRUST,
        "mailing_state": "MN",
        "property_states": ["MN"],
    },
    "slash_name": {
        "name": "STOCKTON/65TH L P",
        "entity_type": EntityType.LP,
        "mailing_state": "CA",
        "property_states": ["CA"],
    },
    "and_present": {
        "name": "SMITH AND JONES LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "TX",
        "property_states": ["TX"],
    },
    "charitable_remainder": {
        "name": "COOK INVESTMENTS L P U/A TRUST COOK CHARITABLE REMAINDER",
        "entity_type": EntityType.TRUST,
        "mailing_state": "CA",
        "property_states": ["CA"],
    },
    "suffix_stripping": {
        "name": "JEFFERSON OWNSBY LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "TX",
        "property_states": ["TX"],
    },
    "no_property_state": {
        "name": "ORPHAN LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "TX",
        "property_states": [],
    },
    "dedupe_minimal": {
        "name": "ACME LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "TX",
        "property_states": ["TX"],
    },
    "max_variants": {
        # & + / + AND + trust + boilerplate would normally generate 7+ variants.
        "name": "FOO & BAR/BAZ AND QUX TRUST THE U/A DTD JAN 1 2020",
        "entity_type": EntityType.TRUST,
        "mailing_state": "CA",
        "property_states": ["CA"],
    },
    "us_territory": {
        "name": "ISLA BONITA LLC",
        "entity_type": EntityType.LLC,
        "mailing_state": "PR",
        "property_states": ["VI"],
    },
}
