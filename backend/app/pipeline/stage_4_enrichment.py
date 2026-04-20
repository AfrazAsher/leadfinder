from app.events import emit_log
from app.state import AppState


async def enrich_contacts(state: AppState, entity) -> None:
    await emit_log(
        state,
        "INFO",
        f"[Enrich stub] Would enrich contacts for {entity.entity_name_search!r} "
        f"— no providers wired yet",
    )
