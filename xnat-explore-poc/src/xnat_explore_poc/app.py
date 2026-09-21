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
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from .config import settings

# No handler exists until this runs - unlike report-viewer's
# logging_setup.configure(), this PoC has no structured-JSON logging
# infrastructure, so a plain getLogger(__name__) call goes nowhere
# (Python's root logger has no handler by default; uvicorn only
# configures its own "uvicorn"/"uvicorn.access" loggers, not this one).
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    # A signed, short-lived claim of who this invoke is actually for -
    # see actions.mint_user_assertion's docstring on report-viewer's side.
    # Required, not optional: the bearer token above only proves the
    # caller knows a shared secret, not who the end user is - an App that
    # skips this has no way to independently catch a bug in
    # report-viewer's own group-gating.
    x_report_viewer_user_assertion: str | None = Header(default=None),
) -> dict:
    if (
        not settings.invoke_token
        or not x_report_viewer_action_token
        or not hmac.compare_digest(x_report_viewer_action_token, settings.invoke_token)
    ):
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()

    if not settings.assertion_key or not x_report_viewer_user_assertion:
        raise HTTPException(status_code=401, detail="missing user assertion")
    try:
        claims = jwt.decode(
            x_report_viewer_user_assertion,
            settings.assertion_key,
            algorithms=["HS256"],
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="user assertion expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="invalid user assertion")
    # Binds the assertion to this exact request - a captured assertion
    # can't be replayed against a different search within its 60s window.
    if claims.get("search_id") != body.get("search_id"):
        raise HTTPException(status_code=401, detail="user assertion search_id mismatch")
    if settings.required_group and settings.required_group not in (
        claims.get("groups") or []
    ):
        raise HTTPException(status_code=403, detail="caller lacks required group")

    # The cohort itself (search_id/sql/username/reports/cohort_truncated -
    # see report-viewer's invoke_search_action) is otherwise unused - a
    # real "Explore in XNAT" action would use `reports` (concrete
    # primary_report_identifier/accession_number pairs, not the raw sql)
    # to resolve or create the matching XNAT project/session and link
    # straight there. Logged as a count only, not the identifiers
    # themselves - primary_report_identifier/accession_number are
    # PHI-adjacent, and this line exists purely so an operator can confirm
    # a real (correctly-sized) cohort crossed the wire, not to make the
    # cohort's contents inspectable via Loki.
    reports = body.get("reports") or []
    log.info(
        "invoke: search_id=%s sub=%s groups=%s reports=%d truncated=%s",
        body.get("search_id"),
        claims.get("sub"),
        claims.get("groups"),
        len(reports),
        body.get("cohort_truncated"),
    )
    return {"url": f"{settings.xnat_base_url}?t={int(time.time())}"}
