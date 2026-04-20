"""Stage 5 — Output generation + confidence scoring.

Populates entity.final_* fields for every resolved entity, writes:
    - CSV (10 columns, 1 row per entity, "Not Found" for un-scored)
    - audit JSON at <csv_path>.audit.json with the full candidate detail

Return value stays a str path (to keep orchestrator.py untouched). The audit
path is derived from the csv path by swapping the suffix.
"""
import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.events import emit_log
from app.models.entity import CleanedEntity
from app.pipeline.officer_selection import select_best_officer, title_priority_score
from app.pipeline.scoring import (
    SOURCE_RELIABILITY,
    STATUS_MULTIPLIER,
    entity_identity_score,
    tier_for_score,
)
from app.state import AppState

OUTPUT_COLUMNS = [
    "#",
    "LLC Company",
    "Decision Maker Name",
    "Parent/Company Name",
    "Website",
    "Job Title",
    "LinkedIn",
    "Email",
    "Phone Number",
    "Confidence",
]


def audit_path_for(csv_path: str) -> Path:
    return Path(csv_path).with_suffix(".audit.json")


def _populate_final_fields(entity: CleanedEntity) -> dict:
    """Run officer selection + scoring. Mutate entity. Return audit dict."""
    audit_entry: dict = {
        "entity_name_cleaned": entity.entity_name_cleaned,
        "status": entity.status,
        "sos_source": entity.sos_source,
        "sos_result": None,
        "selection": None,
        "confidence": None,
    }

    if entity.status != "resolved" or not entity.sos_results:
        return audit_entry

    sos = entity.sos_results[0]
    audit_entry["sos_result"] = sos
    officers = sos.get("officers") or []

    if not officers:
        entity.final_decision_maker = None
        entity.final_confidence = 0.0
        entity.final_tier = "blank"
        audit_entry["selection"] = {
            "chosen_index": None,
            "chosen_officer": None,
            "title_priority_score": 0,
            "reasoning": "SOS returned entity but no officers listed",
        }
        audit_entry["confidence"] = {
            "final_score": 0.0,
            "tier": "blank",
            "components": {
                "source_reliability": SOURCE_RELIABILITY.get(
                    entity.sos_source or "", 0.5
                ),
                "status_multiplier": STATUS_MULTIPLIER.get(
                    (sos.get("status") or "").strip().lower(), 0.7
                ),
                "title_normalized": 0.0,
                "completeness": 0.0,
            },
        }
        return audit_entry

    best = select_best_officer(officers)
    if best is None:
        return audit_entry

    chosen_index = next(
        (i for i, o in enumerate(officers) if o is best), None
    )
    priority_score = title_priority_score(best.get("title"))
    entity.final_decision_maker = best.get("name")
    entity.final_job_title = best.get("title")
    entity.final_address = best.get("address") or sos.get("principal_address")

    score = entity_identity_score(entity, best)
    rounded = round(score, 3)
    tier = tier_for_score(score)
    entity.final_confidence = rounded
    entity.final_tier = tier

    reasoning = (
        f"Highest priority match in title {best.get('title')!r}: "
        f"priority score {priority_score}"
        if priority_score > 0
        else "No priority phrase matched; fell back to first officer"
    )

    audit_entry["selection"] = {
        "chosen_index": chosen_index,
        "chosen_officer": {
            "name": best.get("name"),
            "title": best.get("title"),
            "address": best.get("address"),
        },
        "title_priority_score": priority_score,
        "reasoning": reasoning,
    }
    audit_entry["confidence"] = {
        "final_score": rounded,
        "tier": tier,
        "components": {
            "source_reliability": SOURCE_RELIABILITY.get(
                entity.sos_source or "", 0.5
            ),
            "status_multiplier": STATUS_MULTIPLIER.get(
                (sos.get("status") or "").strip().lower(), 0.7
            ),
            "title_normalized": min(priority_score / 100.0, 1.0),
            "completeness": _completeness_snapshot(entity, best),
        },
    }
    return audit_entry


def _completeness_snapshot(entity: CleanedEntity, winning_officer: dict) -> float:
    sos = entity.sos_results[0] if entity.sos_results else {}
    score = 0.0
    if sos.get("filing_number"):
        score += 0.4
    if sos.get("principal_address"):
        score += 0.3
    if winning_officer.get("address"):
        score += 0.3
    return score


def _csv_row(index: int, entity: CleanedEntity) -> list:
    tier = entity.final_tier or "blank"
    decision_maker = entity.final_decision_maker
    if not decision_maker or tier == "blank":
        decision_maker_cell = "Not Found"
    else:
        decision_maker_cell = decision_maker
    return [
        index,
        entity.entity_name_cleaned,
        decision_maker_cell,
        entity.final_parent_company or "",
        entity.final_website or "",
        entity.final_job_title or "",
        entity.final_linkedin or "",
        entity.final_email or "",
        entity.final_phone or "",
        tier,
    ]


async def generate_output(state: AppState) -> str:
    """Build CSV + audit JSON. Mutate entities with final_* fields. Return CSV path."""
    audit_entries: list[dict] = []
    for entity in state.entities:
        audit_entries.append(_populate_final_fields(entity))

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    tmp.close()
    csv_path = tmp.name

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for i, entity in enumerate(state.entities, start=1):
            writer.writerow(_csv_row(i, entity))

    run_id = state.current_run.run_id if state.current_run else "unknown"
    audit_doc = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_entities": len(state.entities),
        "entities": audit_entries,
    }
    audit_path = audit_path_for(csv_path)
    audit_path.write_text(
        json.dumps(audit_doc, indent=2, default=str), encoding="utf-8"
    )

    await emit_log(
        state, "INFO",
        f"Output CSV written: {len(state.entities)} rows; audit JSON: {audit_path.name}",
    )
    return csv_path
