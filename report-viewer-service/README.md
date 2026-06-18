# scout-report-viewer

FastAPI service that holds Scout search/query results outside of Open WebUI.

See `docs/internal/report-viewer-service-plan.md` for the design context and the
phased build plan. This README is for local dev only.

## Local dev

```bash
cd report-viewer-service
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

# Defaults expect a local Postgres + Trino. Override via env:
export REPORT_VIEWER_DATABASE_URL="postgresql://searches:searches@localhost:5432/searches"
export REPORT_VIEWER_TRINO_HOST="localhost"
export REPORT_VIEWER_TRINO_PORT="8080"

python -m scout_report_viewer
# → http://localhost:8000/healthz
```

## In the cluster

Deployed via `ansible/roles/report_viewer_service` → `helm/report-viewer-service`.
The chart's env block is rendered from
`ansible/roles/report_viewer_service/templates/values.yaml.j2`.
