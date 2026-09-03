"""Tests for /api/plots.

Postgres-backed, so skipped unless `REPORT_VIEWER_TEST_DATABASE_URL` is set.
See test_searches.py for the one-liner to start one.

Viewing a chart re-runs its query, so queue a Trino response per request.
"""

from __future__ import annotations

import json

from scout_report_viewer.config import settings

BAR = {
    "mark": "bar",
    "encoding": {
        "x": {"field": "modality", "type": "nominal"},
        "y": {"field": "n", "type": "quantitative"},
    },
}


CSV = b"accession_number\nACC1\nACC2\nACC1\n"
COHORT_SQL = (
    "SELECT modality, COUNT(*) n FROM reports_latest WHERE {{cohort}} GROUP BY modality"
)


def _create_from_file(client, auth_headers, sql=COHORT_SQL, csv=CSV):
    return client.post(
        "/api/plots/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={"sql": sql, "vega_lite_spec": json.dumps(BAR)},
        headers=auth_headers,
    )


def _create(
    client,
    auth_headers,
    spec=None,
    sql="SELECT modality, COUNT(*) n FROM t",
    explanation=None,
):
    body = {"sql": sql, "vega_lite_spec": spec if spec is not None else BAR}
    if explanation is not None:
        body["sql_explanation"] = explanation
    return client.post("/api/plots", json=body, headers=auth_headers)


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
    assert detail["truncated"] is False


def test_viewing_a_chart_truncates_at_cap(
    client, auth_headers, fake_trino, monkeypatch
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    plot_id = _create(client, auth_headers).json()["id"]
    monkeypatch.setattr(settings, "max_cohort_rows", 3, raising=False)
    # Endpoint fetches cap+1 (=4) to detect overflow; return 4 rows.
    fake_trino(
        ["modality", "n"], [{"modality": m, "n": i} for i, m in enumerate("ABCD")]
    )
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert detail["truncated"] is True
    assert len(detail["rows"]) == 3
    assert "LIMIT 4" in fake_trino.calls[-1][0]


def test_the_listing_returns_metadata_newest_first(client, auth_headers, fake_trino):
    for _ in range(2):
        fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    first = _create(client, auth_headers, explanation="one").json()["id"]
    second = _create(client, auth_headers, explanation="two").json()["id"]
    listed = client.get("/api/plots", headers=auth_headers).json()
    assert [p["id"] for p in listed] == [second, first]
    assert listed[0]["sql_explanation"] == "two"
    # The spec and rows stay server-side until the chart route asks for them.
    assert "spec" not in listed[0] and "rows" not in listed[0]


def test_the_listing_only_shows_your_own_charts(
    client, auth_headers, other_auth_headers, fake_trino
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    _create(client, auth_headers)
    assert client.get("/api/plots", headers=other_auth_headers).json() == []


def test_a_csv_chart_re_runs_bound_to_the_uploaded_ids(
    client, auth_headers, fake_trino
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 2}])
    plot_id = _create_from_file(client, auth_headers).json()["id"]

    fake_trino(["modality", "n"], [{"modality": "MR", "n": 2}])
    assert client.get(f"/api/plots/{plot_id}", headers=auth_headers).status_code == 200

    create_sql, create_params = fake_trino.calls[0]
    view_sql, view_params = fake_trino.calls[1]
    # Deduped, and bound rather than interpolated.
    assert create_params == [["ACC1", "ACC2"]]
    assert view_params == [["ACC1", "ACC2"]]
    assert view_sql == create_sql
    assert "{{cohort}}" not in view_sql
    assert 'contains(?, "accession_number")' in view_sql


def test_a_csv_chart_without_the_cohort_placeholder_is_refused(
    client, auth_headers, fake_trino
):
    r = _create_from_file(
        client,
        auth_headers,
        sql="SELECT modality, COUNT(*) n FROM reports_latest GROUP BY modality",
    )
    assert r.status_code == 400
    assert "{{cohort}}" in r.json()["detail"]


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


def test_a_legend_bind_object_is_refused(client, auth_headers, fake_trino):
    spec = {
        "mark": "bar",
        "params": [{"name": "sel", "select": "point", "bind": {"legend": True}}],
        "encoding": {"x": {"field": "a", "type": "nominal"}},
    }
    r = _create(client, auth_headers, spec=spec)
    assert r.status_code == 400
    assert "bind" in r.json()["detail"]


def test_a_spec_that_fails_to_render_is_refused(client, auth_headers, fake_trino):
    spec = {"mark": {"point": {"size": 100}}, "encoding": {}}
    r = _create(client, auth_headers, spec=spec)
    assert r.status_code == 400
    assert "render" in r.json()["detail"]


def test_a_field_not_in_the_query_columns_is_refused(client, auth_headers, fake_trino):
    fake_trino(["modality", "n"], [])
    fake_trino(["n"], [{"n": 1}])
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "modality", "type": "nominal"},
            "y": {"field": "count", "type": "quantitative"},
        },
    }
    r = _create(client, auth_headers, spec=spec)
    assert r.status_code == 400
    assert "count" in r.json()["detail"]


def test_a_model_chosen_color_scheme_is_stripped(client, auth_headers, fake_trino):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "modality", "type": "nominal"},
            "y": {"field": "n", "type": "quantitative"},
            "color": {
                "field": "modality",
                "type": "nominal",
                "scale": {"scheme": "category10", "domain": ["MR", "CT"]},
            },
        },
    }
    plot_id = _create(client, auth_headers, spec=spec).json()["id"]
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    scale = detail["spec"]["encoding"]["color"]["scale"]
    assert "scheme" not in scale
    assert scale["domain"] == ["MR", "CT"]


def test_usermeta_is_stripped(client, auth_headers, fake_trino):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    spec = {**BAR, "usermeta": {"embedOptions": {"ast": False}}}
    plot_id = _create(client, auth_headers, spec=spec).json()["id"]
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert "usermeta" not in detail["spec"]


def test_nested_data_is_stripped(client, auth_headers, fake_trino):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    spec = {"layer": [{**BAR, "data": {"values": [{"modality": "FAKE", "n": 999}]}}]}
    plot_id = _create(client, auth_headers, spec=spec).json()["id"]
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert "data" not in detail["spec"]["layer"][0]


def test_report_bodies_never_reach_the_browser(client, auth_headers, fake_trino):
    rows = [{"modality": "MR", "report_text": "PHI narrative"}]
    fake_trino(["modality", "report_text"], rows)
    plot_id = _create(client, auth_headers).json()["id"]
    fake_trino(["modality", "report_text"], rows)
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert detail["rows"] == [{"modality": "MR"}]


def test_the_explanation_and_sql_come_back_for_the_explain_panel(
    client, auth_headers, fake_trino
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    plot_id = _create(
        client, auth_headers, explanation="Scan counts by modality."
    ).json()["id"]
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert detail["sql_explanation"] == "Scan counts by modality."
    assert detail["sql"] == "SELECT modality, COUNT(*) n FROM t"


def test_an_omitted_explanation_reads_back_as_empty_not_null(
    client, auth_headers, fake_trino
):
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    plot_id = _create(client, auth_headers).json()["id"]
    fake_trino(["modality", "n"], [{"modality": "MR", "n": 7}])
    detail = client.get(f"/api/plots/{plot_id}", headers=auth_headers).json()
    assert detail["sql_explanation"] == ""
