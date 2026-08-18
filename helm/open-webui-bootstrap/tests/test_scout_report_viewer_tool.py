"""Unit tests for scout_report_viewer_tool's `_post`/`_post_multipart`.

Run with:
    cd helm/open-webui-bootstrap
    PYTHONPATH=files/payloads uvx --with pytest-asyncio --with httpx pytest tests/test_scout_report_viewer_tool.py -v
"""

import importlib.util
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
    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_MESSAGE):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, oauth=oauth)
    assert not called


@pytest.mark.asyncio
async def test_post_multipart_without_bearer_raises_and_skips_request(monkeypatch):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(SessionExpiredError, match=_SESSION_EXPIRED_MESSAGE):
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
async def test_post_upstream_error_still_raised_with_bearer_present(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"detail": "bearer token validation failed"})

    tool = _tool_with_transport(handler, monkeypatch)
    with pytest.raises(
        ReportViewerServiceError, match="bearer token validation failed"
    ):
        await tool._post("/api/searches", {"sql": "SELECT 1"}, oauth="expired-token")


def test_error_text_omits_prefix_for_session_expired():
    exc = SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
    assert Tools._error_text(exc, "Failed") == _SESSION_EXPIRED_MESSAGE


def test_error_text_keeps_prefix_for_other_errors():
    exc = ReportViewerServiceError("report-viewer is temporarily unavailable")
    assert (
        Tools._error_text(exc, "Failed")
        == "Failed: report-viewer is temporarily unavailable"
    )
