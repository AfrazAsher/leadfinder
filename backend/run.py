"""
Programmatic entrypoint for LeadFinder on Windows.

Why this file exists:
    Playwright's subprocess launch requires the Proactor event loop on
    Windows. Uvicorn's `--reload` mode spawns a separate worker process
    whose event loop policy isn't guaranteed to match, which breaks
    Playwright's chromium.launch(). Setting the policy here, before the
    uvicorn import, guarantees the right loop is used from the start.

Usage:
    On Windows:  python run.py
    On macOS/Linux either works; `python run.py` or `uvicorn app.main:app --reload`.
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
