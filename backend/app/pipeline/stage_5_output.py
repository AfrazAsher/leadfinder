import csv
import tempfile

from app.events import emit_log
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


async def generate_output(state: AppState) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    tmp.close()
    path = tmp.name

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)

        for i, entity in enumerate(state.entities, start=1):
            writer.writerow([
                i,
                entity.entity_name_cleaned,
                entity.final_decision_maker or "Not Found",
                entity.final_parent_company or "",
                entity.final_website or "",
                entity.final_job_title or "",
                entity.final_linkedin or "",
                entity.final_email or "",
                entity.final_phone or "",
                entity.final_confidence or "",
            ])

    await emit_log(state, "INFO", f"Output CSV written: {len(state.entities)} rows")
    return path
