import re
from typing import Optional

from app.models.entity import CleanedEntity, EntityType

US_STATE_CODES: set[str] = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

SUFFIX_FORMS_BY_TYPE: dict[EntityType, list[str]] = {
    EntityType.LLC: ["LLC"],
    EntityType.LLLP: ["LLLP"],
    EntityType.LP: ["LP", "L P"],
    EntityType.INC: ["INC", "CORPORATION"],
    EntityType.CORP: ["CORP", "CORPORATION"],
    EntityType.LTD: ["LTD"],
    EntityType.TRUST: ["TRUST", "TR", "TRS"],
    EntityType.PARTNERSHIP: ["PARTNERSHIP", "P/S"],
    EntityType.OTHER: [],
}

MAX_VARIANTS = 5

_UA_DTD_GREEDY = re.compile(r"\bU/A\s+DTD\b.*", re.IGNORECASE)
_UA_BARE = re.compile(r"\bU/A\b", re.IGNORECASE)
_DTD_BARE = re.compile(r"\bDTD\b", re.IGNORECASE)
_TRAILING_THE = re.compile(r"\s+THE\s*$", re.IGNORECASE)
_CHARITABLE_REMAINDER = re.compile(r"\s*\bCHARITABLE\s+REMAINDER\b.*", re.IGNORECASE)
_MULTI_WS = re.compile(r"\s+")
_AND_WORD = re.compile(r"\bAND\b")


def _collapse(name: str) -> str:
    return _MULTI_WS.sub(" ", name).strip()


def _strip_trust_boilerplate(name: str) -> str:
    name = _CHARITABLE_REMAINDER.sub("", name)
    name = _UA_DTD_GREEDY.sub("", name)
    name = _UA_BARE.sub("", name)
    name = _DTD_BARE.sub("", name)
    name = _collapse(name)
    name = _TRAILING_THE.sub("", name)
    return _collapse(name)


def _strip_suffix(name: str, entity_type: EntityType) -> Optional[str]:
    for form in SUFFIX_FORMS_BY_TYPE.get(entity_type, []):
        pattern = re.compile(rf"\s+{re.escape(form)}\s*$", re.IGNORECASE)
        stripped = pattern.sub("", name).rstrip()
        if stripped and stripped != name:
            return stripped
    return None


def _swap_and_ampersand(name: str) -> list[str]:
    out: list[str] = []
    if "&" in name:
        swapped = _collapse(re.sub(r"\s*&\s*", " AND ", name))
        if swapped and swapped != name:
            out.append(swapped)
    if _AND_WORD.search(name):
        swapped = _collapse(_AND_WORD.sub("&", name))
        swapped = re.sub(r"\s*&\s*", " & ", swapped).strip()
        if swapped and swapped != name:
            out.append(swapped)
    return out


def _slash_variants(name: str) -> list[str]:
    if "/" not in name:
        return []
    space_form = _collapse(name.replace("/", " "))
    and_form = _collapse(name.replace("/", " AND "))
    out: list[str] = []
    if space_form and space_form != name:
        out.append(space_form)
    if and_form and and_form != name and and_form != space_form:
        out.append(and_form)
    return out


def _build_filing_states(entity: CleanedEntity) -> list[str]:
    if entity.entity_type == EntityType.TRUST:
        return []

    candidates: list[str] = []

    mailing_state = (entity.mailing_address.state or "").strip().upper()
    if mailing_state in US_STATE_CODES:
        candidates.append(mailing_state)

    for parcel in entity.source_parcels:
        prop_state = (parcel.property_state or "").strip().upper()
        if prop_state in US_STATE_CODES and prop_state not in candidates:
            candidates.append(prop_state)

    if "DE" not in candidates:
        candidates.append("DE")

    return candidates


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_entity(entity: CleanedEntity) -> CleanedEntity:
    normalized = entity.entity_name_normalized
    pre_strip_form: Optional[str] = None

    if entity.entity_type == EntityType.TRUST:
        stripped = _strip_trust_boilerplate(normalized)
        if stripped != normalized:
            pre_strip_form = normalized
        primary = stripped
    else:
        primary = normalized

    primary = _collapse(primary)
    entity.entity_name_search = primary

    variants: list[str] = [primary]
    variants.extend(_swap_and_ampersand(primary))
    variants.extend(_slash_variants(primary))
    suffix_stripped = _strip_suffix(primary, entity.entity_type)
    if suffix_stripped:
        variants.append(suffix_stripped)
    if pre_strip_form:
        variants.append(pre_strip_form)

    variants = _dedupe_preserving_order(variants)[:MAX_VARIANTS]
    entity.search_name_variants = variants

    entity.filing_state_candidates = _build_filing_states(entity)

    return entity
