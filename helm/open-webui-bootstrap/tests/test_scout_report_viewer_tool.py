"""Unit tests for scout_report_viewer_tool's `_post`/`_post_multipart`.

Run with:
    cd helm/open-webui-bootstrap
    PYTHONPATH=files/payloads uvx --with pytest-asyncio --with httpx pytest tests/test_scout_report_viewer_tool.py -v
"""

import asyncio
import importlib.util
import re
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


@pytest.mark.parametrize("oauth", [None, {"access_token": ""}])
@pytest.mark.asyncio
async def test_post_without_bearer_raises_and_skips_request(oauth, monkeypatch):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, oauth=oauth)
    assert not called


@pytest.mark.parametrize("oauth", [None, {"access_token": ""}])
@pytest.mark.asyncio
async def test_get_without_bearer_raises_and_skips_request(oauth, monkeypatch):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._get("/api/plots/abc123", oauth=oauth)
    assert not called


@pytest.mark.parametrize(
    "oauth,expected",
    [("tok123", "Bearer tok123"), ({"access_token": "tok456"}, "Bearer tok456")],
)
@pytest.mark.asyncio
async def test_get_with_bearer_forwards_authorization_header(
    oauth, expected, monkeypatch
):
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    result = await tool._get("/api/plots/abc123", oauth=oauth)
    assert result == {"ok": True}
    assert seen["authorization"] == expected


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


@pytest.mark.asyncio
async def test_post_multipart_without_bearer_raises_and_skips_request(monkeypatch):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._post_multipart(
            "/api/reports/import",
            files={"file": ("x.csv", b"a,b")},
            data={},
            oauth=None,
        )
    assert not called


@pytest.mark.parametrize(
    "oauth,expected",
    [("tok123", "Bearer tok123"), ({"access_token": "tok456"}, "Bearer tok456")],
)
@pytest.mark.asyncio
async def test_post_with_bearer_forwards_authorization_header(
    oauth, expected, monkeypatch
):
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    tool = _tool_with_transport(handler, monkeypatch)
    result = await tool._post("/api/searches", {"sql": "SELECT 1"}, oauth=oauth)
    assert result == {"ok": True}
    assert seen["authorization"] == expected


@pytest.mark.asyncio
async def test_post_upstream_401_with_bearer_present_raises_session_expired(
    monkeypatch,
):
    """This internal call never sets oauth2-proxy's trust header, so a 401
    from report-viewer here can only mean the bearer itself was rejected
    (e.g. a stale token OWUI hasn't refreshed yet) - same user action as a
    missing bearer, so it gets the same friendly message."""

    def handler(request):
        return httpx.Response(401, json={"detail": "bearer token validation failed"})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_PATTERN):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, oauth="expired-token")


@pytest.mark.asyncio
async def test_post_upstream_non_401_error_still_raised_with_bearer_present(
    monkeypatch,
):
    def handler(request):
        return httpx.Response(500, json={"detail": "trino unavailable"})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(ReportViewerServiceError, match="trino unavailable"):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, oauth="valid-token")


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
                    "row_count": 3,
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
                "count": 5,
                "sample": [],
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
