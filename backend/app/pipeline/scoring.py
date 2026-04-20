"""Confidence scoring + tier mapping for Stage 5."""
from typing import Optional

from app.models.entity import CleanedEntity
from app.pipeline.officer_selection import title_priority_score

SOURCE_RELIABILITY: dict[str, float] = {
    "fl_direct": 0.90,
    "nc_direct": 0.90,
    "wa_direct": 0.90,
    "ut_direct": 0.90,
    "serper_linkedin": 0.60,
    "hunter_email": 0.70,
    "apollo_phone": 0.75,
}

STATUS_MULTIPLIER: dict[str, float] = {
    "active": 1.0,
    "current-active": 1.0,
    "current": 1.0,
    "inactive": 0.5,
    "dissolved": 0.3,
    "withdrawn": 0.3,
    "admin dissolved": 0.3,
}

_DEFAULT_SOURCE_RELIABILITY = 0.5
_DEFAULT_STATUS_MULTIPLIER = 0.7


def _completeness(entity: CleanedEntity, winning_officer: dict) -> float:
    if not entity.sos_results:
        return 0.0
    sos = entity.sos_results[0]
    score = 0.0
    if sos.get("filing_number"):
        score += 0.4
    if sos.get("principal_address"):
        score += 0.3
    if winning_officer.get("address"):
        score += 0.3
    return score


def entity_identity_score(
    entity: CleanedEntity, winning_officer: dict
) -> float:
    """
    Base formula (per step_4_confidence_scoring.md):
        score = source_rel * status_mult * (0.7 + 0.15 * title_norm + 0.15 * completeness)

    Clamped to [0.0, 1.0].
    """
    source_rel = SOURCE_RELIABILITY.get(
        entity.sos_source or "", _DEFAULT_SOURCE_RELIABILITY
    )

    sos = entity.sos_results[0] if entity.sos_results else {}
    status_text = (sos.get("status") or "").strip().lower()
    status_mult = STATUS_MULTIPLIER.get(status_text, _DEFAULT_STATUS_MULTIPLIER)

    title_norm = min(
        title_priority_score(winning_officer.get("title")) / 100.0, 1.0
    )
    completeness = _completeness(entity, winning_officer)

    score = source_rel * status_mult * (
        0.7 + 0.15 * title_norm + 0.15 * completeness
    )
    return max(0.0, min(1.0, score))


def tier_for_score(score: Optional[float]) -> str:
    if score is None:
        return "blank"
    if score >= 0.85:
        return "HIGH"
    if score >= 0.70:
        return "MEDIUM-HIGH"
    if score >= 0.55:
        return "MEDIUM"
    if score >= 0.40:
        return "LOW"
    return "blank"
