from typing import Optional

from app.events import emit_log
from app.models.entity import CleanedEntity
from app.providers.base import SOSProvider, SOSResult, ScraperBlocked, ScraperError
from app.providers.browser import BrowserManager
from app.providers.sos_fl import FLSunbizProvider
from app.providers.sos_nc import NCSOSProvider
from app.state import AppState


def build_providers(state: AppState, browser: BrowserManager) -> dict[str, SOSProvider]:
    return {
        "FL": FLSunbizProvider(state, browser),
        "NC": NCSOSProvider(state, browser),
    }


async def lookup_sos(state: AppState, entity: CleanedEntity) -> None:
    browser = state.cache.get("__browser__")
    if browser is None:
        await emit_log(state, "ERROR", "No browser instance available for SOS lookup")
        return

    providers = build_providers(state, browser)
    for candidate_state in entity.filing_state_candidates:
        provider = providers.get(candidate_state)
        if not provider:
            await emit_log(
                state, "INFO",
                f"[SOS] No scraper for {candidate_state} yet; skipping",
            )
            continue
        try:
            result: Optional[SOSResult] = await provider.search(entity)
        except ScraperBlocked as e:
            await emit_log(state, "WARN", f"[SOS {candidate_state}] Blocked: {e}")
            continue
        except ScraperError as e:
            await emit_log(state, "ERROR", f"[SOS {candidate_state}] Error: {e}")
            continue
        except Exception as e:
            await emit_log(
                state, "ERROR",
                f"[SOS {candidate_state}] Unexpected: {type(e).__name__}: {e}",
            )
            continue

        if result:
            entity.sos_results = [result.to_dict()]
            entity.sos_source = f"{candidate_state.lower()}_direct"
            await emit_log(
                state, "INFO",
                f"[SOS {candidate_state}] Found: {result.entity_name} "
                f"(#{result.filing_number}, status={result.status}, "
                f"{len(result.officers)} officers)",
            )
            return

    await emit_log(
        state, "INFO",
        f"[SOS] No match for {entity.entity_name_search!r} in "
        f"{entity.filing_state_candidates}",
    )
