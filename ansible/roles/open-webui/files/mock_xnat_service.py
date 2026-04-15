"""
Mock XNAT Service

A lightweight FastAPI application that stands in for the real XNAT API.
The Send to XNAT action POSTs to this service and displays the response,
making the demo feel like a real integration.

Run with: uvicorn mock_xnat_service:app --host 0.0.0.0 --port 8000
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Mock XNAT Service", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/export")
async def export_to_xnat(request: Request):
    """Accept an export request and return a mock success response.

    Accepts any JSON body — the real schema is TBD. Logs the request
    for observability and returns a success envelope echoing key fields.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    log.info("Received export request: %s", body)

    response = {
        "status": "accepted",
        "xnat_job_id": str(uuid.uuid4()),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "project_id": body.get("project_id", "unknown"),
        "accession_count": body.get(
            "total_count", len(body.get("accession_numbers", []))
        ),
        "message": "Export request accepted. Studies will be available shortly.",
    }

    log.info("Returning response: %s", response)
    return JSONResponse(content=response, status_code=202)
