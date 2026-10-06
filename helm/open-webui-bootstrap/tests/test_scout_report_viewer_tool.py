"""Unit tests for scout_report_viewer_tool: the auth path (forwarding the
caller's OWUI token, and renewing it when it is missing or rejected),
chart rendering, and per-turn embed accumulation.

Run with:
    cd helm/open-webui-bootstrap
    PYTHONPATH=files/payloads uvx --with pytest-asyncio --with httpx pytest tests/test_scout_report_viewer_tool.py -v
"""

import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path

import httpx
import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "files/payloads/scout_report_viewer_tool.py"
)
_spec = importlib.util.spec_from_file_location("scout_report_viewer_tool", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

Tools = _mod.Tools
ReportViewerServiceError = _mod.ReportViewerServiceError
SessionExpiredError = _mod.SessionExpiredError
_SESSION_EXPIRED_MESSAGE = _mod._SESSION_EXPIRED_MESSAGE
_SESSION_EXPIRED_PATTERN = re.escape(_SESSION_EXPIRED_MESSAGE)


def _tool_with_transport(handler, monkeypatch):
    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(_mod.httpx, "AsyncClient", _client)
    return Tools()


def _stub_owui_sessions(monkeypatch, tokens):
    """Stand in for OWUI's own token resolution, which `_Auth` imports at
    call time. Each entry in `tokens` answers one lookup."""
    pending = list(tokens)

    async def get_system_oauth_token(request, user):
        return pending.pop(0) if pending else None

    middleware = types.ModuleType("open_webui.utils.middleware")
    middleware.get_system_oauth_token = get_system_oauth_token
    for name, module in (
        ("open_webui", types.ModuleType("open_webui")),
        ("open_webui.utils", types.ModuleType("open_webui.utils")),
        ("open_webui.utils.middleware", middleware),
    ):
        monkeypatch.setitem(sys.modules, name, module)


class _Caller:
    """Records the events a tool sends to the browser."""

    def __init__(self, confirm=True):
        self.events = []
        self._confirm = confirm

    async def __call__(self, event):
        self.events.append(event)
        return self._confirm if event.get("type") == "confirmation" else True

    def types(self):
        return [e.get("type") for e in self.events]

    def code(self):
        return "".join(e.get("data", {}).get("code", "") for e in self.events)


def _auth(monkeypatch, *, oauth=None, tokens=(), caller=None):
    _stub_owui_sessions(monkeypatch, tokens)
    return _mod._Auth(oauth, caller, object(), {"id": "user-1"})


async def _drain_prompts():
    """Let the fire-and-forget sign-in prompt run to completion."""
    await asyncio.gather(*list(_mod._PENDING_PROMPTS))


@pytest.mark.parametrize(
    "oauth,expected",
    [("tok123", "Bearer tok123"), ({"access_token": "tok456"}, "Bearer tok456")],
)
@pytest.mark.asyncio
async def test_post_forwards_the_callers_token(oauth, expected, monkeypatch):
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(monkeypatch, oauth=oauth)
    result = await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    assert result == {"ok": True}
    assert seen["authorization"] == expected


@pytest.mark.parametrize(
    "oauth,expected",
    [("tok123", "Bearer tok123"), ({"access_token": "tok456"}, "Bearer tok456")],
)
@pytest.mark.asyncio
async def test_get_forwards_the_callers_token(oauth, expected, monkeypatch):
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(monkeypatch, oauth=oauth)
    result = await tool._get("/api/plots/abc123", auth=auth)
    assert result == {"ok": True}
    assert seen["authorization"] == expected


@pytest.mark.asyncio
async def test_token_that_expired_mid_turn_is_refreshed_without_the_browser(
    monkeypatch,
):
    """The commonest case: a multi-query answer outlives the 5-minute access
    token OWUI resolved at the start of the turn. The session behind it is
    fine, so re-reading OWUI's session refreshes the token and no browser
    round trip happens."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        if len(seen) == 1:
            return httpx.Response(401, json={"detail": "bearer token invalid"})
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    caller = _Caller()
    auth = _auth(
        monkeypatch,
        oauth="expired",
        tokens=[{"access_token": "refreshed"}],
        caller=caller,
    )

    assert await tool._get("/api/plots/abc123", auth=auth) == {"ok": True}
    assert seen == ["Bearer expired", "Bearer refreshed"]
    assert caller.events == []


@pytest.mark.parametrize("oauth", [None, {"access_token": ""}])
@pytest.mark.asyncio
async def test_missing_token_renews_in_the_browser_then_retries(oauth, monkeypatch):
    """OWUI deleted the session when its refresh token turned out to belong
    to an SSO session that already ended, so the turn starts with no token
    at all."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    caller = _Caller()
    # No session to read at first; the browser round trip mints one.
    auth = _auth(
        monkeypatch,
        oauth=oauth,
        tokens=[None, {"access_token": "renewed"}],
        caller=caller,
    )

    assert await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth) == {
        "ok": True
    }
    assert seen == ["Bearer renewed"]
    assert caller.types() == ["execute"]
    assert "/oauth/oidc/login" in caller.code()


@pytest.mark.asyncio
async def test_rejected_token_is_retried_with_a_renewed_one(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        if len(seen) == 1:
            return httpx.Response(401, json={"detail": "bearer token invalid"})
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(
        monkeypatch,
        oauth="stale",
        tokens=[None, {"access_token": "renewed"}],
        caller=_Caller(),
    )

    assert await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth) == {
        "ok": True
    }
    assert seen == ["Bearer stale", "Bearer renewed"]


@pytest.mark.asyncio
async def test_session_renewed_by_an_earlier_call_skips_the_browser(monkeypatch):
    """`__oauth_token__` is resolved once per turn, so a second tool call in
    the same turn still carries the dead token - but OWUI's session is
    already good and no second round trip is needed."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    caller = _Caller()
    auth = _auth(
        monkeypatch, oauth=None, tokens=[{"access_token": "renewed"}], caller=caller
    )

    await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    assert seen == ["Bearer renewed"]
    assert caller.events == []


@pytest.mark.asyncio
async def test_unrenewable_session_raises_and_offers_a_sign_in(monkeypatch):
    """Keycloak's own session is gone, so the frame can't renew silently and
    only an interactive sign-in is left."""
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool = _tool_with_transport(handler, monkeypatch)
    caller = _Caller(confirm=True)
    auth = _auth(monkeypatch, oauth=None, tokens=[None, None], caller=caller)

    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    assert not called
    await _drain_prompts()
    assert caller.types() == ["execute", "confirmation", "execute"]
    assert "window.location.assign" in caller.code()


@pytest.mark.asyncio
async def test_declining_the_sign_in_prompt_does_not_navigate(monkeypatch):
    tool = _tool_with_transport(lambda r: httpx.Response(200, json={}), monkeypatch)
    caller = _Caller(confirm=False)
    auth = _auth(monkeypatch, oauth=None, tokens=[None, None], caller=caller)

    with pytest.raises(SessionExpiredError):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    await _drain_prompts()
    assert caller.types() == ["execute", "confirmation"]
    assert "window.location.assign" not in caller.code()


@pytest.mark.asyncio
async def test_the_same_token_back_is_not_a_renewal(monkeypatch):
    """Renewal has to produce a *different* token; handing back the one that
    was just rejected would only buy a duplicate request."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(401, json={"detail": "bearer token invalid"})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(
        monkeypatch,
        oauth="stale",
        tokens=[{"access_token": "stale"}, {"access_token": "stale"}],
        caller=_Caller(),
    )

    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    await _drain_prompts()
    assert seen == ["Bearer stale"]


@pytest.mark.asyncio
async def test_renewal_is_attempted_once_per_call(monkeypatch):
    auth = _auth(monkeypatch, oauth=None, tokens=[None, None], caller=_Caller())
    assert await auth.renew() is None
    assert await auth.renew() is None


@pytest.mark.asyncio
async def test_no_browser_channel_still_fails_cleanly(monkeypatch):
    """Nothing to drive a renewal with - a background task, say - so report
    the expiry rather than hanging on an event nobody will answer."""
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(monkeypatch, oauth=None, tokens=[None, None], caller=None)

    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    assert not called


@pytest.mark.asyncio
async def test_multipart_renews_on_a_missing_token(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(
        monkeypatch,
        oauth=None,
        tokens=[None, {"access_token": "renewed"}],
        caller=_Caller(),
    )

    result = await tool._post_multipart(
        "/api/reports/import",
        files={"file": ("x.csv", b"a,b")},
        data={},
        auth=auth,
    )
    assert result == {"ok": True}
    assert seen == ["Bearer renewed"]


@pytest.mark.asyncio
async def test_non_401_errors_are_not_treated_as_expiry(monkeypatch):
    caller = _Caller()

    def handler(request):
        return httpx.Response(500, json={"detail": "trino unavailable"})

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(monkeypatch, oauth="valid-token", caller=caller)

    with pytest.raises(ReportViewerServiceError, match="trino unavailable"):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)
    assert caller.events == []


@pytest.mark.asyncio
async def test_unreachable_service_is_not_treated_as_expiry(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(monkeypatch, oauth="valid-token", caller=_Caller())

    with pytest.raises(ReportViewerServiceError, match="temporarily unavailable"):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, auth=auth)


@pytest.mark.asyncio
async def test_timeout_is_not_retried_or_treated_as_expiry(monkeypatch):
    """A query that ran out the clock must not run twice."""
    calls = []
    caller = _Caller()

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow")

    tool = _tool_with_transport(handler, monkeypatch)
    auth = _auth(monkeypatch, oauth="valid-token", caller=caller)

    with pytest.raises(_mod.ServiceTimeoutError):
        await tool._post("/api/plots", {"sql": "SELECT 1"}, auth=auth)
    assert len(calls) == 1
    assert caller.events == []


def test_render_chart_data_includes_sql_explanation_and_rows():
    plot = {
        "sql": "SELECT modality, COUNT(*) AS n FROM reports_latest GROUP BY 1",
        "sql_explanation": "Report counts by modality.",
        "rows": [{"modality": "CT", "n": 3}, {"modality": "MRI", "n": 1}],
    }
    text = Tools._render_chart_data(plot)
    assert "Report counts by modality." in text
    assert "SELECT modality, COUNT(*)" in text
    assert "| modality | n |" in text
    assert "CT" in text and "MRI" in text
    assert "do not call" in text.lower()


def test_render_chart_data_handles_no_rows():
    plot = {"sql": "SELECT 1", "sql_explanation": "", "rows": []}
    text = Tools._render_chart_data(plot)
    assert "no rows" in text.lower()


def test_error_text_omits_prefix_for_session_expired():
    exc = SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
    assert Tools._error_text(exc, "Failed") == _SESSION_EXPIRED_MESSAGE


def test_error_text_keeps_prefix_for_other_errors():
    exc = ReportViewerServiceError("report-viewer is temporarily unavailable")
    assert (
        Tools._error_text(exc, "Failed")
        == "Failed: report-viewer is temporarily unavailable"
    )


# --- per-turn embed accumulation ---------------------------------------------


@pytest.fixture(autouse=True)
def _clear_turn_embeds():
    _mod._TURN_EMBEDS.clear()
    yield
    _mod._TURN_EMBEDS.clear()


class _Emitter:
    """Records every event. `first_embed_delay` stalls the first embeds send
    so that a second one landing first is observable."""

    def __init__(self, first_embed_delay=0.0):
        self.events = []
        self._first_embed_delay = first_embed_delay
        self._embeds_seen = 0

    async def __call__(self, event):
        if event["type"] == "embeds":
            self._embeds_seen += 1
            if self._embeds_seen == 1 and self._first_embed_delay:
                await asyncio.sleep(self._first_embed_delay)
        self.events.append(event)

    @property
    def last_embeds(self):
        embeds = [e for e in self.events if e["type"] == "embeds"]
        return embeds[-1]["data"]["embeds"] if embeds else []


def _routing_handler():
    """Answers /api/searches and /api/plots with ids derived from a counter,
    so each call yields a distinguishable view_url."""
    counts = {"searches": 0, "plots": 0}

    def handler(request):
        if request.url.path.startswith("/api/plots"):
            counts["plots"] += 1
            n = counts["plots"]
            return httpx.Response(
                200,
                json={
                    "id": f"pl_{n}",
                    "view_url": f"https://rv/spa/plots/pl_{n}",
                    "columns": ["modality", "n"],
                },
            )
        counts["searches"] += 1
        n = counts["searches"]
        return httpx.Response(
            200,
            json={
                "id": f"ds_{n}",
                "view_url": f"https://rv/spa/searches/ds_{n}",
                "columns": ["primary_report_identifier"],
                "sample": [{"primary_report_identifier": "s3://bucket/1"}],
            },
        )

    return handler


async def _chart(tool, emitter, message_id="m1"):
    return await tool.scout_chart_sql(
        sql="SELECT modality, count(*) n FROM reports_latest GROUP BY modality",
        vega_lite_spec={"mark": "bar"},
        __event_emitter__=emitter,
        __oauth_token__="tok",
        __metadata__={"chat_id": "c1"},
        __message_id__=message_id,
    )


async def _cohort(tool, emitter, message_id="m1"):
    return await tool.scout_find_reports(
        sql="SELECT primary_report_identifier, accession_number FROM reports_latest",
        __event_emitter__=emitter,
        __oauth_token__="tok",
        __metadata__={"chat_id": "c1"},
        __message_id__=message_id,
    )


@pytest.mark.asyncio
async def test_chart_then_cohort_in_one_turn_renders_both(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter()
    await _chart(tool, emitter)
    await _cohort(tool, emitter)
    assert emitter.last_embeds == [
        "https://rv/spa/plots/pl_1",
        "https://rv/spa/searches/ds_1",
    ]


@pytest.mark.asyncio
async def test_second_cohort_in_one_turn_replaces_the_first(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter()
    await _cohort(tool, emitter)
    await _cohort(tool, emitter)
    assert emitter.last_embeds == ["https://rv/spa/searches/ds_2"]


@pytest.mark.asyncio
async def test_superseded_cohort_keeps_charts_and_moves_last(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter()
    await _chart(tool, emitter)
    await _cohort(tool, emitter)
    await _chart(tool, emitter)
    await _cohort(tool, emitter)
    assert emitter.last_embeds == [
        "https://rv/spa/plots/pl_1",
        "https://rv/spa/plots/pl_2",
        "https://rv/spa/searches/ds_2",
    ]


@pytest.mark.asyncio
async def test_charts_capped_per_turn_and_cohort_survives(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter()
    for _ in range(_mod._MAX_CHARTS_PER_TURN + 1):
        await _chart(tool, emitter)
    await _cohort(tool, emitter)
    embeds = emitter.last_embeds
    assert len(embeds) == _mod._MAX_CHARTS_PER_TURN + 1
    assert "https://rv/spa/plots/pl_1" not in embeds
    assert embeds[-1] == "https://rv/spa/searches/ds_1"


@pytest.mark.asyncio
async def test_next_turn_does_not_inherit_previous_embeds(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter()
    await _chart(tool, emitter, message_id="m1")
    await _cohort(tool, emitter, message_id="m2")
    assert emitter.last_embeds == ["https://rv/spa/searches/ds_1"]


@pytest.mark.asyncio
async def test_without_message_id_emits_single_embed_and_stores_nothing(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter()
    await _chart(tool, emitter, message_id=None)
    await _cohort(tool, emitter, message_id=None)
    assert emitter.last_embeds == ["https://rv/spa/searches/ds_1"]
    assert _mod._TURN_EMBEDS == {}


@pytest.mark.asyncio
async def test_parallel_tool_calls_do_not_emit_out_of_order(monkeypatch):
    tool = _tool_with_transport(_routing_handler(), monkeypatch)
    emitter = _Emitter(first_embed_delay=0.05)
    await asyncio.gather(_chart(tool, emitter), _cohort(tool, emitter))
    assert sorted(emitter.last_embeds) == [
        "https://rv/spa/plots/pl_1",
        "https://rv/spa/searches/ds_1",
    ]
