"""Totally fake report-viewer "backend-call" action target (#739 PoC).

This is NOT a real XNAT integration - no cohort correlation, no XNAT
REST calls, nothing project/subject/experiment-shaped. Its only job is
to prove that report-viewer's generic backend-call action mechanism
crosses a real service boundary to a genuinely separate, independently
deployed app (matching issue #595's "Apps" tier), rather than being a
disguised in-process function call. The timestamp query param exists
purely so a click visibly produces a different URL each time - evidence
the backend actually ran, not just served a static link.
"""

from __future__ import annotations

import hmac
import time

from fastapi import FastAPI, Header, HTTPException, Request

from .config import settings

app = FastAPI()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invoke")
async def invoke(
    request: Request,
    # Matches report-viewer's own ActionDescriptor.invoke_token forwarding
    # convention (see report_viewer/routes/searches.py's invoke_search_action).
    x_report_viewer_action_token: str | None = Header(default=None),
) -> dict:
    if not settings.invoke_token or not x_report_viewer_action_token or not hmac.compare_digest(
        x_report_viewer_action_token, settings.invoke_token
    ):
        raise HTTPException(status_code=401, detail="unauthorized")
    # The request body (search_id/sql/username - see report-viewer's
    # ActionInvokeRequest) is intentionally unused. A real "Explore in
    # XNAT" action would use it to resolve or create the matching XNAT
    # project/session and link straight there.
    await request.json()
    return {"url": f"{settings.xnat_base_url}?t={int(time.time())}"}
