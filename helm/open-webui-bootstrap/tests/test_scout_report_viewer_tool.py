"""Smoke tests for scout_report_viewer_tool against a mocked report-viewer.

We're testing the integration shape: bearer forwarding, the iframe URL
in the response, the public_base_url override, and graceful error
handling. Lives next to the chart's existing tests so it ships with the
helm/open-webui-bootstrap suite.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
import respx

# Tool lives in helm/open-webui-bootstrap/files/payloads/. Add to path
# for the duration of the test session.
PAYLOADS = Path(__file__).resolve().parents[1] / "files" / "payloads"
sys.path.insert(0, str(PAYLOADS))

from scout_report_viewer_tool import ReportViewerServiceError, Tools  # noqa: E402


SERVICE = "http://report-viewer.scout-analytics:8000"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@respx.mock
def test_search_reports_returns_iframe_with_view_url():
    create = respx.post(f"{SERVICE}/api/searches").mock(
        return_value=httpx.Response(
            201,
            json={
                "search_id": "s_abc123",
                "count": 42,
                "id_column": "message_control_id",
                "kind": "report",
                "sample": [{"message_control_id": "m1"}],
                "view_url": f"{SERVICE}/spa/searches/s_abc123",
                "summary": "Materialized 42 rows.",
            },
        )
    )
    summary = respx.get(f"{SERVICE}/api/searches/s_abc123/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "search_id": "s_abc123",
                "count": 42,
                "buckets": {
                    "modality": [{"value": "CT", "n": 30}, {"value": "MR", "n": 12}]
                },
            },
        )
    )

    t = Tools()
    resp = _run(
        t.search_reports(sql="SELECT message_control_id FROM r WHERE year=2024")
    )
    assert create.called
    assert summary.called
    body = resp.body.decode()
    assert "s_abc123" in body
    assert "42 reports" in body
    assert "CT (30)" in body  # top modality threaded through
    assert "<iframe" in body
    assert "/spa/searches/s_abc123" in body
    # Total payload size is the whole point: should be tiny (~few hundred B).
    assert len(body) < 1500


@respx.mock
def test_search_reports_forwards_bearer_from_oauth_dict():
    captured = {}

    def _check(request):
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            201,
            json={
                "search_id": "s_xyz",
                "count": 1,
                "id_column": "message_control_id",
                "kind": "report",
                "sample": [],
                "view_url": f"{SERVICE}/spa/searches/s_xyz",
                "summary": "",
            },
        )

    respx.post(f"{SERVICE}/api/searches").mock(side_effect=_check)
    respx.get(f"{SERVICE}/api/searches/s_xyz/summary").mock(
        return_value=httpx.Response(
            200, json={"search_id": "s_xyz", "count": 1, "buckets": {}}
        )
    )

    t = Tools()
    _run(
        t.search_reports(
            sql="SELECT 1",
            __oauth_token__={"access_token": "tok-from-owui", "refresh_token": "r"},
        )
    )
    assert captured["auth"] == "Bearer tok-from-owui"


@respx.mock
def test_search_reports_handles_400_from_service():
    respx.post(f"{SERVICE}/api/searches").mock(
        return_value=httpx.Response(400, json={"detail": "query returned no rows"})
    )

    t = Tools()
    out = _run(t.search_reports(sql="SELECT 1 WHERE false"))
    # Errors return a string (LLM-readable), not an HTMLResponse.
    assert isinstance(out, str)
    assert "no rows" in out
    assert "400" in out


@respx.mock
def test_public_base_url_rewrites_iframe_host():
    respx.post(f"{SERVICE}/api/searches").mock(
        return_value=httpx.Response(
            201,
            json={
                "search_id": "s_pub",
                "count": 1,
                "id_column": "message_control_id",
                "kind": "report",
                "sample": [],
                # Service computed this from in-cluster request host.
                "view_url": f"{SERVICE}/spa/searches/s_pub",
                "summary": "",
            },
        )
    )
    respx.get(f"{SERVICE}/api/searches/s_pub/summary").mock(
        return_value=httpx.Response(
            200, json={"search_id": "s_pub", "count": 1, "buckets": {}}
        )
    )

    t = Tools()
    t.valves.public_base_url = "https://report-viewer.dev02.tag.rcif.io"
    resp = _run(t.search_reports(sql="SELECT 1"))
    body = resp.body.decode()
    assert "https://report-viewer.dev02.tag.rcif.io/spa/searches/s_pub" in body
    # Must not leak the in-cluster URL.
    assert SERVICE not in body


@respx.mock
def test_read_reports_returns_rows_json():
    rows_url = f"{SERVICE}/api/searches/s_qq/rows"
    respx.get(rows_url, params={"page": 1, "limit": 5}).mock(
        return_value=httpx.Response(
            200,
            json={
                "search_id": "s_qq",
                "page": 1,
                "limit": 5,
                "total": 100,
                "columns": ["message_control_id"],
                "rows": [{"message_control_id": "m1"}],
            },
        )
    )
    t = Tools()
    out = _run(t.read_reports(search_id="s_qq", limit=5))
    assert '"total": 100' in out
    assert "m1" in out


def test_read_reports_rejects_oversize_limit():
    t = Tools()
    out = _run(t.read_reports(search_id="s_qq", limit=999))
    assert "Error" in out and "limit" in out
