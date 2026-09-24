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
import html
import logging
import time
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
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

# Two separate FastAPI apps, not one app on two ports: /invoke must be
# structurally unreachable from the public listener, not just
# NetworkPolicy-restricted. A single app object serving both ports would
# expose /invoke on the public one too, since FastAPI doesn't restrict
# routes by which port a request arrived on.
invoke_app = FastAPI()
landing_app = FastAPI()


@invoke_app.get("/healthz")
@landing_app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


_LANDING_PAGE = """\
<!doctype html>
<html>
<head><title>xnat-explore-poc (fake)</title></head>
<body>
<p>Cohort of {reports} reports received for {user}.</p>
<p>This is not a real XNAT integration - see xnat-explore-poc/src/xnat_explore_poc/app.py.</p>
</body>
</html>
"""


@landing_app.get("/", response_class=HTMLResponse)
def landing(reports: str = "0", user: str = "") -> str:
    return _LANDING_PAGE.format(reports=html.escape(reports), user=html.escape(user))


@invoke_app.post("/invoke")
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
        log.warning("invoke rejected: bad or missing action token")
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()

    if not settings.assertion_key or not x_report_viewer_user_assertion:
        log.warning(
            "invoke rejected: search_id=%s missing user assertion", body.get("search_id")
        )
        raise HTTPException(status_code=401, detail="missing user assertion")
    try:
        claims = jwt.decode(
            x_report_viewer_user_assertion,
            settings.assertion_key,
            algorithms=["HS256"],
        )
    except ExpiredSignatureError:
        log.warning(
            "invoke rejected: search_id=%s user assertion expired", body.get("search_id")
        )
        raise HTTPException(status_code=401, detail="user assertion expired")
    except JWTError:
        log.warning(
            "invoke rejected: search_id=%s invalid user assertion signature",
            body.get("search_id"),
        )
        raise HTTPException(status_code=401, detail="invalid user assertion")
    # Binds the assertion to this exact request - a captured assertion
    # can't be replayed against a different search within its 60s window.
    if claims.get("search_id") != body.get("search_id"):
        log.warning(
            "invoke rejected: assertion search_id=%s != request search_id=%s",
            claims.get("search_id"),
            body.get("search_id"),
        )
        raise HTTPException(status_code=401, detail="user assertion search_id mismatch")
    if settings.required_group and settings.required_group not in (
        claims.get("groups") or []
    ):
        log.warning(
            "invoke rejected: search_id=%s sub=%s groups=%s lacks required group %s",
            body.get("search_id"),
            claims.get("sub"),
            claims.get("groups"),
            settings.required_group,
        )
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
    # Points at our own landing page (landing_app, a separate public port -
    # see module docstring), not a real XNAT deployment - self-hosted
    # specifically so this demo doesn't need a real XNAT Ingress's COOP
    # header changed just to prove the popup mechanism works end to end.
    # sub/reports-count are safe to put in a URL (unlike the report
    # identifiers themselves - see the PHI-adjacent note above): neither
    # is used for any authorization decision here, only display, and both
    # already appear in this service's own logs today.
    sub = claims.get("sub") or ""
    url = (
        f"{settings.landing_base_url}/"
        f"?reports={len(reports)}&user={quote(sub)}&t={int(time.time())}"
    )
    return {"url": url}
