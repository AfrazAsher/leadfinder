import asyncio
from datetime import datetime, timezone

from app.core.config import settings
from app.events import broadcast, emit_log
from app.pipeline.stage_2_parsing import parse_entity
from app.pipeline.stage_3_sos import lookup_sos
from app.pipeline.stage_4_enrichment import enrich_contacts
from app.pipeline.stage_5_output import generate_output
from app.providers.browser import BrowserManager
from app.state import AppState


def _cancelled(state: AppState) -> bool:
    return state.current_run is None or state.current_run.status == "cancelled"


async def _emit_stats(state: AppState) -> None:
    r = state.current_run
    if r is None:
        return
    await broadcast(state, "stats", {
        "run_id": r.run_id,
        "status": r.status,
        "current_stage": r.current_stage,
        "entities_total": r.entities_total,
        "entities_processed": r.entities_processed,
        "entities_resolved": r.entities_resolved,
        "entities_unenriched": r.entities_unenriched,
        "entities_failed": r.entities_failed,
    })


async def orchestrate(state: AppState) -> None:
    try:
        await emit_log(state, "INFO", f"Orchestrator started for run {state.current_run.run_id}")

        async with BrowserManager() as browser:
            state.cache["__browser__"] = browser
            try:
                semaphore = asyncio.Semaphore(settings.max_parallel_entities)

                async def process_one(entity):
                    async with semaphore:
                        if _cancelled(state):
                            return
                        try:
                            entity.status = "in_progress"
                            parse_entity(entity)
                            if _cancelled(state):
                                return
                            await lookup_sos(state, entity)
                            if _cancelled(state):
                                return
                            await enrich_contacts(state, entity)

                            if entity.status == "in_progress":
                                if entity.contacts or entity.sos_results:
                                    entity.status = "resolved"
                                else:
                                    entity.status = "unenriched"
                        except Exception as e:
                            entity.status = "failed"
                            entity.error_message = str(e)
                            await emit_log(
                                state, "ERROR",
                                f"Entity {entity.entity_name_cleaned} failed: {e}",
                            )
                        finally:
                            state.current_run.entities_processed += 1
                            if entity.status == "resolved":
                                state.current_run.entities_resolved += 1
                            elif entity.status == "unenriched":
                                state.current_run.entities_unenriched += 1
                            elif entity.status == "failed":
                                state.current_run.entities_failed += 1
                            await _emit_stats(state)

                state.current_run.current_stage = "processing"
                await broadcast(state, "stage", {"stage": "processing", "done": False})
                await asyncio.gather(*[process_one(e) for e in state.entities])

                if _cancelled(state):
                    await emit_log(state, "WARN", "Pipeline cancelled; skipping output stage")
                    state.current_run.completed_at = datetime.now(timezone.utc)
                    return

                state.current_run.current_stage = "output"
                await broadcast(state, "stage", {"stage": "output", "done": False})
                await emit_log(state, "INFO", "Generating output CSV and audit JSON")
                output_path = await generate_output(state)
                state.current_run.output_path = output_path

                state.current_run.current_stage = "output_complete"
                state.current_run.status = "completed"
                state.current_run.completed_at = datetime.now(timezone.utc)

                await emit_log(
                    state, "INFO",
                    f"Pipeline completed: {state.current_run.entities_resolved} resolved, "
                    f"{state.current_run.entities_unenriched} unenriched, "
                    f"{state.current_run.entities_failed} failed",
                )
                await broadcast(state, "done", {
                    "output_path": output_path,
                    "run_id": state.current_run.run_id,
                    "final_stats": {
                        "resolved": state.current_run.entities_resolved,
                        "unenriched": state.current_run.entities_unenriched,
                        "failed": state.current_run.entities_failed,
                        "total": state.current_run.entities_total,
                    },
                })
            finally:
                state.cache.pop("__browser__", None)

    except asyncio.CancelledError:
        if state.current_run is not None:
            state.current_run.status = "cancelled"
            state.current_run.completed_at = datetime.now(timezone.utc)
        await emit_log(state, "WARN", "Orchestrator cancelled")
        raise
    except Exception as e:
        if state.current_run is not None:
            state.current_run.status = "failed"
            state.current_run.error_message = str(e)
            state.current_run.completed_at = datetime.now(timezone.utc)
        await emit_log(state, "ERROR", f"Orchestrator failed: {e}")
        await broadcast(state, "error", {"message": str(e)})
