from app.events import emit_log
from app.state import AppState


async def lookup_sos(state: AppState, entity) -> None:
    await emit_log(
        state,
        "INFO",
        f"[SOS stub] Would query {entity.filing_state_candidates} for "
        f"{entity.entity_name_search!r} — no providers wired yet",
    )
