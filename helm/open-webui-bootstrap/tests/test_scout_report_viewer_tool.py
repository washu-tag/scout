"""Unit tests for scout_report_viewer_tool's `_post`/`_post_multipart`.

Run with:
    cd helm/open-webui-bootstrap
    PYTHONPATH=files/payloads uvx --with pytest-asyncio --with httpx pytest tests/test_scout_report_viewer_tool.py -v
"""

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
