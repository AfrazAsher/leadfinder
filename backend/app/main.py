from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import configure_logging
from app.routers import download, events, health, run
from app.state import AppState, init_quotas


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.pipeline = AppState()
    init_quotas(app.state.pipeline)
    yield
    state = app.state.pipeline
    if state.current_task and not state.current_task.done():
        state.current_task.cancel()


app = FastAPI(title="LeadFinder", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(run.router)
app.include_router(events.router)
app.include_router(download.router)
