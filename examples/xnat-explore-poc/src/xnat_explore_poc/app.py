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


def _scrub_list_for_log(items):
    return [_scrub_for_log(item) for item in items or []]


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


# Issue #739 follow-up: this tab is opened from report-viewer embedded in
# OWUI's chat iframe, which lacks allow-popups-to-escape-sandbox (ADR 0038) -
# per the HTML sandboxing spec, that means this tab inherits OWUI's sandbox
# flags, not just whatever lets it open at all. The static page below would
# render fine under almost any flag set, so it can't surface that - a real
# target (actual XNAT) needs working navigation, dialogs, form submission,
# and often its own popups. These probes test those specific capabilities
# directly, reporting pass/fail in the page itself rather than requiring
# devtools.
_LANDING_PAGE = """\
<!doctype html>
<html>
<head><title>xnat-explore-poc (fake)</title></head>
<body>
<p>Cohort of {reports} reports received for {user}.</p>
<p>This is not a real XNAT integration - see xnat-explore-poc/src/xnat_explore_poc/app.py.</p>

<hr>
<h2>Sandbox inheritance probes (#739)</h2>
<p>If this tab was opened from report-viewer embedded in OWUI's chat, it inherits
   OWUI's iframe sandbox flags unless that iframe sets
   <code>allow-popups-to-escape-sandbox</code>. These test whether a real target
   app's ordinary behavior - navigating, showing a dialog, submitting a form, or
   opening its own popup - would actually work here, not just whether this tab
   was able to open. <code>allow-forms</code> is a separate flag from
   <code>allow-top-navigation</code>: the latter only covers script/anchor-driven
   navigation, not a real <code>&lt;form&gt;</code> submission, which has its own
   gate and fails silently (no console message, unlike a blocked
   <code>alert()</code>) when absent.</p>

<p>There are two distinct navigation keywords: <code>allow-top-navigation</code>
   (unconditional) and <code>allow-top-navigation-by-user-activation</code> (only
   inside a direct click, not after any async gap). A real app's redirects -
   after a fetch, a setTimeout, an SSO hop through a different origin - need the
   unconditional form. The two buttons below distinguish which one, if either,
   is actually present.</p>

<p><button onclick="testSyncNavigation()">Test navigation (synchronous, in-click)</button>
   <span id="nav-sync-result"></span></p>

<p><button onclick="testAsyncNavigation()">Test navigation (after a fetch, async)</button>
   <span id="nav-async-result"></span></p>

<p><button onclick="testModal()">Test modal dialog</button>
   <span id="modal-result"></span></p>

<p><button onclick="testNestedPopup()">Test opening a further popup</button>
   <span id="popup-result"></span></p>

<p><form onsubmit="onFormSubmit()" method="GET" action="" style="display:inline">
     <input type="hidden" name="form_submitted" value="1">
     <button type="submit">Test form submission (real &lt;form&gt;, not fetch/location)</button>
   </form>
   <span id="form-result"></span></p>

<script>
  const params = new URLSearchParams(window.location.search);
  if (params.get("navigated") === "sync") {{
    document.getElementById("nav-sync-result").textContent =
      "PASSED - succeeded inside the click. Doesn't by itself distinguish " +
      "allow-top-navigation from the weaker allow-top-navigation-by-user-activation " +
      "- see the async test.";
  }}
  if (params.get("navigated") === "async") {{
    document.getElementById("nav-async-result").textContent =
      "PASSED even after an async gap - allow-top-navigation (the unconditional " +
      "form) is genuinely present, not just the by-user-activation variant.";
  }}
  if (params.get("form_submitted") === "1") {{
    document.getElementById("form-result").textContent =
      "PASSED - the page reloaded via a real <form> submission (distinct from " +
      "allow-top-navigation, which only covers script/anchor-driven navigation) - " +
      "allow-forms is present.";
  }}

  function testSyncNavigation() {{
    // A real navigation, not history.pushState - pushState doesn't require
    // allow-top-navigation at all, so it wouldn't test anything real here.
    const url = new URL(window.location.href);
    url.searchParams.set("navigated", "sync");
    window.location.href = url.toString();
  }}

  function testAsyncNavigation() {{
    document.getElementById("nav-async-result").textContent =
      "Fetching, then navigating after the response - this is far enough " +
      "outside the click to no longer count as direct user activation...";
    fetch(window.location.pathname).then(() => {{
      const url = new URL(window.location.href);
      url.searchParams.set("navigated", "async");
      window.location.href = url.toString();
    }});
  }}

  function testModal() {{
    window.alert("If you can see this dialog, allow-modals is effectively present.");
    document.getElementById("modal-result").textContent =
      "Button clicked - if no dialog appeared just now, it was silently blocked " +
      "(allow-modals is absent).";
  }}

  function testNestedPopup() {{
    const w = window.open("about:blank", "_blank");
    document.getElementById("popup-result").textContent = w
      ? "PASSED - a further popup opened (allow-popups is effectively present)."
      : "FAILED - window.open() returned null (allow-popups is absent, or a " +
        "popup blocker intervened).";
  }}

  function onFormSubmit() {{
    // Does not preventDefault - the real test is whether the browser's own
    // submission algorithm is allowed to navigate afterwards, not whether this
    // handler ran (the submit event itself doesn't require allow-forms, only
    // the resulting navigation does). This just gives immediate feedback that
    // the click registered, in case the submission itself is silently blocked
    // (no error, no console message - the page just never reloads).
    document.getElementById("form-result").textContent =
      "Submit event fired - watching for a reload to confirm the navigation " +
      "itself wasn't silently blocked...";
  }}
</script>
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
    # report-viewer's own role-gating.
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
            "invoke rejected: search_id=%s missing user assertion",
            _scrub_for_log(body.get("search_id")),
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
            "invoke rejected: search_id=%s user assertion expired",
            _scrub_for_log(body.get("search_id")),
        )
        raise HTTPException(status_code=401, detail="user assertion expired")
    except JWTError:
        log.warning(
            "invoke rejected: search_id=%s invalid user assertion signature",
            _scrub_for_log(body.get("search_id")),
        )
        raise HTTPException(status_code=401, detail="invalid user assertion")
    # Catches a NAIVE replay - reusing a captured assertion+body wholesale
    # against a different search without updating this field. Does NOT
    # bind the request body to the assertion: both values here are visible
    # to (and settable by) anyone holding the assertion, so this alone
    # doesn't stop someone from keeping search_id matched while
    # substituting a different `reports` list. Neither this check nor
    # anything else in this handler verifies that `reports` is what
    # report-viewer actually resolved - see
    # helm/report-viewer/values.yaml's assertionKey doc comment for what
    # this invoke boundary does and doesn't guarantee. That gap is only
    # reachable by bypassing report-viewer entirely (network access to
    # this endpoint plus a valid invoke token plus a captured assertion) -
    # not through report-viewer's own UI, which never trusts
    # client-supplied report ids.
    if claims.get("search_id") != body.get("search_id"):
        log.warning(
            "invoke rejected: assertion search_id=%s != request search_id=%s",
            _scrub_for_log(claims.get("search_id")),
            _scrub_for_log(body.get("search_id")),
        )
        raise HTTPException(status_code=401, detail="user assertion search_id mismatch")
    if settings.required_role and settings.required_role not in (
        claims.get("roles") or []
    ):
        log.warning(
            "invoke rejected: search_id=%s sub=%s roles=%s lacks required role %s",
            _scrub_for_log(body.get("search_id")),
            _scrub_for_log(claims.get("sub")),
            _scrub_list_for_log(claims.get("roles")),
            settings.required_role,
        )
        raise HTTPException(status_code=403, detail="caller lacks required role")

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
        "invoke: search_id=%s sub=%s roles=%s reports=%d truncated=%s",
        _scrub_for_log(body.get("search_id")),
        _scrub_for_log(claims.get("sub")),
        _scrub_list_for_log(claims.get("roles")),
        len(reports),
        _scrub_for_log(body.get("cohort_truncated")),
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
