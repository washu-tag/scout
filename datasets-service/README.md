# scout-datasets

FastAPI service that holds Scout cohort/query results outside of Open WebUI.

See `docs/internal/datasets-service-plan.md` for the design context and the
phased build plan. This README is for local dev only.

## Local dev

```bash
cd datasets-service
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

# Defaults expect a local Postgres + Trino. Override via env:
export DATASETS_DATABASE_URL="postgresql://datasets:datasets@localhost:5432/datasets"
export DATASETS_TRINO_HOST="localhost"
export DATASETS_TRINO_PORT="8080"

python -m scout_datasets
# → http://localhost:8000/healthz
```

## In the cluster

Deployed via `ansible/roles/datasets_service` → `helm/datasets-service`.
The chart's env block is rendered from
`ansible/roles/datasets_service/templates/values.yaml.j2`.
