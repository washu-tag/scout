"""
Mock XNAT Service

A lightweight FastAPI application that stands in for the real XNAT API.
The Send to XNAT action POSTs to this service and displays the response,
making the demo feel like a real integration.

Run with: uvicorn mock_xnat_service:app --host 0.0.0.0 --port 8000
"""

import logging
import random
import time
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
    """Accept an IQ data request and return a mock XNAT IQ response.

    Mirrors the real XNAT Image Query (IQ) data request API schema.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    log.info("Received export request: %s", body)

    response = {
        "id": random.randint(1, 9999),
        "requestUser": body.get("requestUser", "unknown"),
        "requestDate": int(time.time() * 1000),
        "status": "PRE_PROCESSING",
        "deidStatus": "IDENTIFIABLE",
        "comment": body.get("comment", ""),
        "message": "Request is being pre-processed.",
        "projectName": body.get("projectName", "unknown"),
        "irbNumber": body.get("irbNumber", ""),
        "numberOfItemsRequested": len(body.get("data", [])),
        "numberOfItemsPreviouslyDownloaded": 0,
        "hasIrbProtocolForm": False,
    }

    log.info("Returning response: %s", response)
    return JSONResponse(content=response, status_code=202)
