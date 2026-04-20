import re
from typing import Any, Optional

import pandas as pd

from app.models.entity import (
    CleanedEntity,
    EntityType,
    MailingAddress,
    SourceParcel,
)
from app.models.report import CleaningReport

COLUMN_MAP: dict[str, str] = {
    "FIRST_NAME": "first_name",
    "LAST_NAME": "last_name",
    "OWNER_NAME_1": "owner_name",
    "MAILING_ADDRESS": "mailing_address",
    "MAILING_CITY": "mailing_city",
    "MAILING_STATE": "mailing_state",
    "MAILING_ZIP": "mailing_zip",
    "PROPERTY_ADDRESS": "property_address",
    "PROPERTY_CITY": "property_city",
    "PROPERTY_STATE": "property_state",
    "PROPERTY_ZIP": "property_zip",
    "APN": "apn",
    "COUNTY": "county",
    "PRIORITY": "priority",
}

REQUIRED_CANONICAL: set[str] = {"owner_name", "mailing_state", "property_state", "apn"}

ENTITY_SUFFIXES: set[str] = {"LLC", "LLLP", "LP", "INC", "CORP", "LTD", "TRUST", "FARMS"}

BUSINESS_KEYWORDS: set[str] = {
    "LLC", "INC", "CORP", "LTD", "LP", "LLLP", "TRUST", "COMPANY",
    "FARMS", "HOLDINGS", "INVESTMENTS", "PARTNERS", "ENTERPRISES",
    "FOUNDATION", "PARTNERSHIP", "GROUP", "PROPERTIES", "ASSOCIATES",
}

GOVERNMENT_WHOLE_WORDS: set[str] = {"COUNTY", "DISTRICT", "MUNICIPAL"}
GOVERNMENT_PHRASES: list[str] = [
    "STATE OF", "CITY OF", "TOWN OF", "DEPARTMENT OF", "PUBLIC UTILITY",
]

RELIGIOUS_WHOLE_WORDS: set[str] = {
    "CHURCH", "MINISTRY", "TEMPLE", "MOSQUE",
    "SYNAGOGUE", "DIOCESE", "PARISH", "CATHEDRAL",
}

PROBATE_SUBSTRINGS: list[str] = ["ESTATE OF", "DECEASED", "PROBATE"]

SENTINEL_SUBSTRINGS: list[str] = ["NOT AVAILABLE", "UNKNOWN", "SEE NOTES", "WITHHELD"]
SENTINEL_EXACT: set[str] = {"N/A", "NONE"}

# LLLP must come before LP to avoid partial-match replacement
PERIOD_ENTITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bL\.L\.L\.P\.", re.IGNORECASE), "LLLP"),
    (re.compile(r"\bL\.L\.C\.", re.IGNORECASE), "LLC"),
    (re.compile(r"\bL\.P\.", re.IGNORECASE), "LP"),
    (re.compile(r"\bINC\.", re.IGNORECASE), "INC"),
    (re.compile(r"\bCORP\.", re.IGNORECASE), "CORP"),
    (re.compile(r"\bLTD\.", re.IGNORECASE), "LTD"),
]


def _word_tokens(name: str) -> set[str]:
    return set(re.findall(r"[A-Z0-9]+", name.upper()))


def _cell_str(val: Any) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _cell_or_none(val: Any) -> Optional[str]:
    s = _cell_str(val)
    return s if s else None


def _normalize_zip(val: Any) -> Optional[str]:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if not s:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    return digits[:5].zfill(5) if len(digits) < 5 else digits[:5]


def _matches_sentinel(name_upper: str) -> bool:
    if len(name_upper) < 3:
        return True
    if name_upper in SENTINEL_EXACT:
        return True
    return any(sub in name_upper for sub in SENTINEL_SUBSTRINGS)


def _has_government_keyword(name_upper: str) -> bool:
    if GOVERNMENT_WHOLE_WORDS & _word_tokens(name_upper):
        return True
    return any(
        re.search(rf"\b{re.escape(phrase)}\b", name_upper) for phrase in GOVERNMENT_PHRASES
    )


def _has_religious_keyword(name_upper: str) -> bool:
    return bool(RELIGIOUS_WHOLE_WORDS & _word_tokens(name_upper))


def _has_probate_keyword(name_upper: str) -> bool:
    return any(sub in name_upper for sub in PROBATE_SUBSTRINGS)


def _normalize_entity_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\s*&\s*", " & ", name)
    name = name.rstrip(",").rstrip()
    for pattern, replacement in PERIOD_ENTITY_PATTERNS:
        name = pattern.sub(replacement, name)
    return name


def _detect_entity_type(normalized_upper: str) -> EntityType:
    tokens = normalized_upper.split()
    if not tokens:
        return EntityType.OTHER

    def _match(token: str) -> Optional[EntityType]:
        if token == "LLC":
            return EntityType.LLC
        if token == "LLLP":
            return EntityType.LLLP
        if token == "LP":
            return EntityType.LP
        if token in {"INC", "CORPORATION"}:
            return EntityType.INC
        if token == "CORP":
            return EntityType.CORP
        if token == "LTD":
            return EntityType.LTD
        if token in {"TRUST", "TR", "TRS"}:
            return EntityType.TRUST
        if token in {"PARTNERSHIP", "P/S"}:
            return EntityType.PARTNERSHIP
        return None

    result = _match(tokens[-1])
    if result is not None:
        return result
    if len(tokens) >= 2:
        result = _match(tokens[-2])
        if result is not None:
            return result
    return EntityType.OTHER


def clean_csv(path: str) -> CleaningReport:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)

    header_map: dict[str, str] = {}
    for col in df.columns:
        normalized = col.strip().replace(" ", "_").upper()
        if normalized in COLUMN_MAP:
            header_map[col] = COLUMN_MAP[normalized]

    df = df[list(header_map.keys())].rename(columns=header_map)

    missing = REQUIRED_CANONICAL - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required canonical columns: {sorted(missing)}"
        )

    input_rows = len(df)

    individuals_skipped = 0
    government_skipped = 0
    religious_skipped = 0
    probate_skipped = 0
    sentinel_skipped = 0
    data_error_skipped = 0
    misclassified_individual_skipped = 0

    kept_groups: dict[str, list[dict[str, Any]]] = {}

    for _, raw_row in df.iterrows():
        row = raw_row.to_dict()

        owner = _cell_str(row.get("owner_name"))
        owner_upper = owner.upper()

        first_raw = _cell_str(row.get("first_name"))
        first = first_raw.upper()
        last = _cell_str(row.get("last_name"))

        if not owner_upper:
            data_error_skipped += 1
            continue

        if _matches_sentinel(owner_upper):
            sentinel_skipped += 1
            continue

        if _has_government_keyword(owner_upper):
            government_skipped += 1
            continue

        if _has_religious_keyword(owner_upper):
            religious_skipped += 1
            continue

        if _has_probate_keyword(owner_upper):
            probate_skipped += 1
            continue

        if first in ENTITY_SUFFIXES and last:
            data_error_skipped += 1
            continue

        if last and first not in ENTITY_SUFFIXES:
            individuals_skipped += 1
            continue

        if not last:
            tokens = owner_upper.split()
            if 2 <= len(tokens) <= 3 and not (BUSINESS_KEYWORDS & set(tokens)):
                misclassified_individual_skipped += 1
                continue

        cleaned_name = _normalize_entity_name(owner)
        normalized_upper = cleaned_name.upper()

        kept_groups.setdefault(normalized_upper, []).append({
            "row": row,
            "entity_name_raw": owner,
            "entity_name_cleaned": cleaned_name,
            "entity_name_normalized": normalized_upper,
            "first_name_raw": first_raw,
        })

    entities: list[CleanedEntity] = []

    for normalized_upper, group in kept_groups.items():
        seen_parcels: set[tuple] = set()
        parcels: list[SourceParcel] = []
        for item in group:
            r = item["row"]
            apn = _cell_str(r.get("apn"))
            prop_addr = _cell_or_none(r.get("property_address"))
            prop_city = _cell_or_none(r.get("property_city"))
            prop_state = _cell_str(r.get("property_state"))
            county = _cell_or_none(r.get("county"))
            key = (apn, prop_addr, prop_city, prop_state, county)
            if key in seen_parcels:
                continue
            seen_parcels.add(key)
            parcels.append(
                SourceParcel(
                    apn=apn,
                    property_address=prop_addr,
                    property_city=prop_city,
                    property_state=prop_state,
                    county=county,
                )
            )

        first_mailing: Optional[MailingAddress] = None
        mailing_tuples: set[tuple] = set()

        for item in group:
            r = item["row"]
            street = _cell_or_none(r.get("mailing_address"))
            city = _cell_or_none(r.get("mailing_city"))
            state = _cell_or_none(r.get("mailing_state"))
            zip_ = _normalize_zip(r.get("mailing_zip"))
            if first_mailing is None and street is not None:
                complete = all(v for v in (street, city, state, zip_))
                first_mailing = MailingAddress(
                    street=street,
                    city=city,
                    state=state,
                    zip=zip_,
                    complete=complete,
                )
            if any(v is not None for v in (street, city, zip_)):
                mailing_tuples.add((street, city, zip_))

        if first_mailing is None:
            first_mailing = MailingAddress()

        is_priority = any(
            _cell_str(item["row"].get("priority")).lower() == "yes"
            for item in group
        )

        quality_flags: list[str] = []

        cleaned_display = group[0]["entity_name_cleaned"]
        cleaned_len = len(cleaned_display)
        if 3 <= cleaned_len <= 8 and not (BUSINESS_KEYWORDS & _word_tokens(cleaned_display)):
            quality_flags.append("cryptic_name")

        if any(len(item["first_name_raw"]) == 30 for item in group):
            quality_flags.append("truncated_source_name")

        if not first_mailing.complete:
            quality_flags.append("mailing_address_incomplete")

        if len(mailing_tuples) > 1:
            quality_flags.append("mailing_address_conflict")

        entity_type = _detect_entity_type(normalized_upper)

        if entity_type == EntityType.TRUST and (
            "U/A" in normalized_upper or "DTD" in normalized_upper
        ):
            quality_flags.append("trust_with_legal_boilerplate")

        entities.append(
            CleanedEntity(
                entity_name_raw=group[0]["entity_name_raw"],
                entity_name_cleaned=cleaned_display,
                entity_name_normalized=normalized_upper,
                entity_type=entity_type,
                mailing_address=first_mailing,
                source_parcels=parcels,
                is_priority=is_priority,
                quality_flags=quality_flags,
            )
        )

    unique_entities = len(entities)

    skip_summary_text = (
        f"Processed {input_rows} rows -> {unique_entities} unique entities kept. "
        f"Skipped: {individuals_skipped} individuals, "
        f"{government_skipped} government, "
        f"{religious_skipped} religious, "
        f"{probate_skipped} probate, "
        f"{sentinel_skipped} sentinel, "
        f"{data_error_skipped} data-error, "
        f"{misclassified_individual_skipped} misclassified-individual."
    )

    return CleaningReport(
        input_rows=input_rows,
        unique_entities=unique_entities,
        individuals_skipped=individuals_skipped,
        government_skipped=government_skipped,
        religious_skipped=religious_skipped,
        probate_skipped=probate_skipped,
        sentinel_skipped=sentinel_skipped,
        data_error_skipped=data_error_skipped,
        misclassified_individual_skipped=misclassified_individual_skipped,
        entities=entities,
        skip_summary_text=skip_summary_text,
    )
