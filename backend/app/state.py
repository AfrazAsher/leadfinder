import asyncio
from collections import deque
from typing import Optional

from app.models.entity import CleanedEntity
from app.models.run import RunState


class AppState:
    def __init__(self):
        self.current_run: Optional[RunState] = None
        self.entities: list[CleanedEntity] = []
        self.current_task: Optional[asyncio.Task] = None
        self.log_buffer: deque[dict] = deque(maxlen=500)
        self.sse_subscribers: set[asyncio.Queue] = set()
        self.cache: dict[str, tuple] = {}
        self.quotas: dict[str, dict] = {}
        self.quota_lock = asyncio.Lock()

    def is_busy(self) -> bool:
        if self.current_run is None:
            return False
        return self.current_run.status == "running"

    def reset_for_new_run(self) -> None:
        self.entities = []
        self.log_buffer.clear()


def init_quotas(state: AppState) -> None:
    state.quotas = {
        "opencorporates": {"calls_made": 0, "calls_limit": 200},
        "serper":         {"calls_made": 0, "calls_limit": 2500},
        "hunter":         {"calls_made": 0, "calls_limit": 25},
        "apollo":         {"calls_made": 0, "calls_limit": 60},
    }
