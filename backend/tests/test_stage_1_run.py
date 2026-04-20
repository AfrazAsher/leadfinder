import asyncio
import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state import AppState, init_quotas

FIXTURE_CSV_PATH = Path(__file__).parent / "fixtures" / "sample_input.csv"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_still_works(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_post_run_with_valid_csv_returns_202(client):
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r = client.post("/api/run", files=files)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert "run_id" in body
    assert body["cleaning_summary"]["input_rows"] == 14


def test_post_run_with_non_csv_returns_400(client):
    files = {"file": ("foo.txt", io.BytesIO(b"not a csv"), "text/plain")}
    r = client.post("/api/run", files=files)
    assert r.status_code == 400


def test_download_before_run_returns_404(client):
    r = client.get("/api/download")
    assert r.status_code == 404


def test_delete_when_no_active_run(client):
    r = client.delete("/api/run")
    assert r.status_code == 200
    assert r.json()["status"] == "no_active_run"


@pytest.mark.asyncio
async def test_events_endpoint_returns_sse_content_type():
    # TestClient/httpx.ASGITransport buffers the full response body and hangs on
    # infinite SSE streams, so we drive the ASGI app directly to capture the
    # response-start headers and then short-circuit via http.disconnect.
    app.state.pipeline = AppState()
    init_quotas(app.state.pipeline)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/events",
        "raw_path": b"/api/events",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
    }

    captured: dict = {}
    start_received = asyncio.Event()
    pending = [
        {"type": "http.request", "body": b"", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive():
        if pending:
            return pending.pop(0)
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
            captured["headers"] = {
                k.decode(): v.decode() for k, v in message.get("headers", [])
            }
            start_received.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(start_received.wait(), timeout=5.0)
    finally:
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    assert captured["status"] == 200
    assert captured["headers"].get("content-type", "").startswith("text/event-stream")


def test_end_to_end_run_and_download(client):
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r = client.post("/api/run", files=files)
    assert r.status_code == 200

    start = time.time()
    while time.time() - start < 10:
        r2 = client.get("/api/download")
        if r2.status_code == 200:
            break
        time.sleep(0.2)
    else:
        pytest.fail("Run did not complete within 10 seconds")

    r3 = client.get("/api/download")
    assert r3.status_code == 200
    assert r3.headers["content-type"].startswith("text/csv")
    text = r3.text
    assert "LLC Company" in text
    assert text.count("\n") >= 4


def test_concurrent_post_returns_409(client):
    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r1 = client.post("/api/run", files=files)
    assert r1.status_code == 200

    with open(FIXTURE_CSV_PATH, "rb") as f:
        files = {"file": ("sample.csv", f, "text/csv")}
        r2 = client.post("/api/run", files=files)
    assert r2.status_code in (200, 409)
    if r2.status_code == 409:
        detail = r2.json()["detail"]
        assert detail["error"] == "run_in_progress"
        assert "current_run" in detail
