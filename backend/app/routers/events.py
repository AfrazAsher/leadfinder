import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.events import format_sse
from app.state import AppState

router = APIRouter()

POLL_INTERVAL_SECONDS = 0.5
KEEPALIVE_EVERY_N_POLLS = 30  # ~15s at 0.5s poll


@router.get("/api/events")
async def events(request: Request):
    state: AppState = request.app.state.pipeline

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        state.sse_subscribers.add(queue)
        try:
            yield ":connected\n\n"

            for payload in list(state.log_buffer):
                yield format_sse(payload)

            polls_since_keepalive = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=POLL_INTERVAL_SECONDS
                    )
                    yield format_sse(payload)
                    polls_since_keepalive = 0
                    if payload["event"] in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    polls_since_keepalive += 1
                    if polls_since_keepalive >= KEEPALIVE_EVERY_N_POLLS:
                        yield ":keepalive\n\n"
                        polls_since_keepalive = 0
        finally:
            state.sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
