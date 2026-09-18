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
import logging
import time

from fastapi import FastAPI, Header, HTTPException, Request

from .config import settings

log = logging.getLogger(__name__)

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
    if (
        not settings.invoke_token
        or not x_report_viewer_action_token
        or not hmac.compare_digest(x_report_viewer_action_token, settings.invoke_token)
    ):
        raise HTTPException(status_code=401, detail="unauthorized")
    # The cohort itself (search_id/sql/username/reports/cohort_truncated -
    # see report-viewer's invoke_search_action) is otherwise unused - a
    # real "Explore in XNAT" action would use `reports` (concrete
    # primary_report_identifier/accession_number pairs, not the raw sql)
    # to resolve or create the matching XNAT project/session and link
    # straight there. Logged here purely so an operator can confirm the
    # real cohort crossed the wire end to end, not just a plausible-looking
    # request shape.
    body = await request.json()
    reports = body.get("reports") or []
    log.info(
        "invoke: search_id=%s username=%s reports=%d truncated=%s ids=%s",
        body.get("search_id"),
        body.get("username"),
        len(reports),
        body.get("cohort_truncated"),
        reports,
    )
    return {"url": f"{settings.xnat_base_url}?t={int(time.time())}"}
