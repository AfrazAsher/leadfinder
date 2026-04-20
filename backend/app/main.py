from fastapi import FastAPI

from app.core.logging import configure_logging
from app.routers import health

configure_logging()

app = FastAPI(title="LeadFinder", version="0.1.0")
app.include_router(health.router)
