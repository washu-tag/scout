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

from .config import settings

# No handler exists until this runs - unlike report-viewer's
# logging_setup.configure(), this PoC has no structured-JSON logging
# infrastructure, so a plain getLogger(__name__) call goes nowhere
# (Python's root logger has no handler by default; uvicorn only
# configures its own "uvicorn"/"uvicorn.access" loggers, not this one).
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)


def _scrub_for_log(v):
    """Strip CR/LF before a value lands in a log message - no JSON
    formatter here to do it for us. body.get(...) is untyped (raw JSON,
    no Pydantic model), so CodeQL can't rule out a non-str reaching
    here - no isinstance branch, so there's no unsanitized path out."""
    return str(v).replace("\r", "").replace("\n", "")


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
    # The only credential this endpoint checks - see config.py's invoke_token
    # comment for why that's enough given this endpoint's network isolation.
    x_report_viewer_action_token: str | None = Header(default=None),
) -> dict:
    if (
        not settings.invoke_token
        or not x_report_viewer_action_token
        or not hmac.compare_digest(x_report_viewer_action_token, settings.invoke_token)
    ):
        log.warning("invoke rejected: bad or missing action token")
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()

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
    sub = body.get("username") or ""
    log.info(
        "invoke: search_id=%s sub=%s reports=%d truncated=%s",
        _scrub_for_log(body.get("search_id")),
        _scrub_for_log(sub),
        len(reports),
        _scrub_for_log(body.get("cohort_truncated")),
    )
    # Points at our own landing page (landing_app, a separate public port -
    # see module docstring and config.py's landing_page_port), not a real
    # XNAT deployment. sub/reports-count are safe to put in a URL (unlike
    # the report identifiers themselves - see the PHI-adjacent note
    # above): neither is used for any authorization decision here, only
    # display, and both already appear in this service's own logs today.
    url = (
        f"{settings.landing_base_url}/"
        f"?reports={len(reports)}&user={quote(sub)}&t={int(time.time())}"
    )
    return {"url": url}
