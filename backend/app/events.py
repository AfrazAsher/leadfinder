import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.state import AppState


async def broadcast(
    state: AppState,
    event_type: str,
    data: dict[str, Any],
) -> None:
    payload = {
        "event": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    state.log_buffer.append(payload)

    for queue in list(state.sse_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass


async def subscribe(state: AppState) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    state.sse_subscribers.add(queue)

    try:
        yield ":connected\n\n"

        for payload in list(state.log_buffer):
            yield format_sse(payload)

        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield format_sse(payload)
                if payload["event"] in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield ":keepalive\n\n"
    finally:
        state.sse_subscribers.discard(queue)


def format_sse(payload: dict) -> str:
    data_json = json.dumps(payload)
    return f"event: {payload['event']}\ndata: {data_json}\n\n"


async def emit_log(state: AppState, level: str, message: str) -> None:
    await broadcast(state, "log", {
        "level": level,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
