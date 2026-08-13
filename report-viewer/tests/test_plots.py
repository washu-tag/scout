"""Tests for /api/plots.

Postgres-backed, so skipped unless `REPORT_VIEWER_TEST_DATABASE_URL` is set.
See test_searches.py for the one-liner to start one.

Viewing a chart re-runs its query, so queue a Trino response per request.
"""

from __future__ import annotations

BAR = {
    "mark": "bar",
    "encoding": {
        "x": {"field": "modality", "type": "nominal"},
        "y": {"field": "n", "type": "quantitative"},
    },
}


def _create(client, auth_headers, spec=None, sql="SELECT modality, COUNT(*) n FROM t"):
    return client.post(
        "/api/plots",
        json={"sql": sql, "vega_lite_spec": spec if spec is not None else BAR},
        headers=auth_headers,
    )


def test_create_returns_a_view_url_and_no_chart_payload(
    client, auth_headers, fake_trino
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    body = _create(client, auth_headers).json()
    assert body["row_count"] == 1
    assert body["view_url"].endswith(f"/spa/plots/{body['id']}")
    assert "spec" not in body and "rows" not in body


def test_viewing_a_chart_re_runs_its_sql(client, auth_headers, fake_trino):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    plot_id = _create(client, auth_headers).json()["id"]
    # Different numbers prove it re-evaluated rather than replayed.
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 9}])
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert detail["rows"] == [{"modality": "MR", "n": 9}]
    assert detail["spec"]["mark"] == "bar"
    assert "data" not in detail["spec"]


def test_another_user_cannot_open_someone_elses_chart(
    client, auth_headers, other_auth_headers, fake_trino
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    plot_id = _create(client, auth_headers).json()["id"]
    r = client.get(f"/api/plots/{plot_id}", headers=other_auth_headers)
    assert r.status_code == 404


def test_a_spec_carrying_any_url_is_refused(client, auth_headers, fake_trino):
    nested = {
        "mark": "bar",
        "encoding": {"x": {"field": "a", "type": "nominal"}},
        "layer": [{"data": {"url": "https://evil.example/x.json"}}],
    }
    r = _create(client, auth_headers, spec=nested)
    assert r.status_code == 400
    assert "url" in r.json()["detail"]


def test_report_bodies_never_reach_the_browser(client, auth_headers, fake_trino):
    rows = [{"modality": "MR", "report_text": "PHI narrative"}]
    fake_trino(["modality", "report_text"], rows)
    plot_id = _create(client, auth_headers).json()["id"]
    fake_trino(["modality", "report_text"], rows)
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert detail["rows"] == [{"modality": "MR"}]
