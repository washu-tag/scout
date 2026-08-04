# Scout - Radiology Report Explorer

## Overview

Scout is a distributed data analytics platform for exploring HL7 radiology reports. It
processes HL7 messages into a Delta Lake using a medallion architecture (bronze → silver)
and exposes them through SQL, dashboards, notebooks, and chat.

User-facing docs: https://washu-scout.readthedocs.io/en/latest/ (source in `docs/source/`).
Developer docs: `docs/internal/`. Issues and planning: GitHub issues and projects on
`washu-tag/scout` (templates in `.github/ISSUE_TEMPLATE/`).

## Architecture

Microservices on Kubernetes (K3s), deployed by Ansible + Helm, fronted by Traefik.

```
HL7 log files → hl7log-extractor (Temporal workflow) → MinIO bronze (raw HL7)
                                                     → hl7-transformer → Delta Lake silver
                                                     → Trino → Superset / JupyterHub / report-viewer
```

- **Lake**: MinIO (S3-compatible storage), Hive Metastore (catalog), Delta Lake (table
  format), Trino (query engine, the only read path for user-facing clients)
- **Ingest**: Temporal orchestrates; `hl7log-extractor` splits log files into messages,
  `hl7-transformer` (PySpark) parses and writes Delta. An opt-in real-time MLLP listener
  path exists in observer mode (ADR 0028)
- **User services**: Superset (analytics), JupyterHub (notebooks, query via the `scout`
  SDK — no Spark in the image), launchpad (landing page + user admin), Open WebUI + Ollama
  and report-viewer (chat, optional), XNAT (imaging, optional)
- **AuthN/AuthZ**: Keycloak + oauth2-proxy at the ingress; Trino authorization via OPA
  (ADRs 0003, 0020–0025)
- **Infrastructure**: PostgreSQL (CloudNativePG), Cassandra (K8ssandra) and Elasticsearch
  (ECK) for Temporal, Valkey (cache/websockets), Prometheus + Loki + Grafana, optional
  NVIDIA GPU operator

Services are reachable under `external_url` via Traefik (launchpad at `/`, others at
`/superset`, `/jupyter`, `/grafana`, `/temporal`, …) and in-cluster at
`<service>.<namespace>.svc.cluster.local`.

## Project Structure

```
ansible/              # Deployment: playbooks/ (one per component), roles/, filter_plugins/
                      #   scout_common/     shared defaults, tasks, filters
                      #   group_vars/all/versions.yaml   all pinned component versions
                      #   inventory.yaml    site config (created from inventory.example.yaml)
helm/                 # Chart configurations and Scout-authored charts
extractor/
  hl7log-extractor/   # Java/Gradle: Temporal workflow + activities, log → bronze
  hl7-transformer/    # Python/PySpark package `hl7scout`: HL7 → Delta silver
hl7-listener/         # Java/Gradle: Camel MLLP listener + Kafka batcher (ADR 0028)
report-viewer/        # Python FastAPI + React frontend/ (ADR 0029)
launchpad/            # Next.js: landing page + /admin/users console (ADR 0025)
sdk/python/           # The `scout` SDK used by notebooks and Voila
keycloak/             # Keycloak image + event-listener/ SPI (OPA bundles, scout-users API)
policy/trino/         # OPA Rego policy for Trino authorization
analytics/notebooks/  # Notebooks shipped to JupyterHub
tooling/              # CI tooling (manifest/ = build-manifest schema + reader/writer)
docs/                 # source/ = user docs, internal/ = developer docs + adr/
tests/                # ingest/ auth/ data-authorization/ network/ (see docs/internal/integration_tests.md)
orchestrator/         # Docs only: how to launch workflows from the Temporal CLI
```

Note the workflow code lives in `extractor/hl7log-extractor`, not `orchestrator/`.

## Data Schema

The silver layer's `reports` table holds one row per HL7 radiology report, partitioned by
`year` (derived from `message_dt`), with patient identifiers, order/service fields,
personnel, full `report_text`, parsed report sections, and arrays of `patient_ids` and
`diagnoses`. Derivative tables and the `_epic_view` family are built on top of it.

`docs/source/dataschema.md` is the authoritative column list and HL7 field mapping — read
it rather than guessing at column names. Patient-identifier handling has its own note in
`docs/internal/patient_ids.md`. Two columns are internal: `updated` (last content change)
and `content_hash` (re-ingest dedup, ADR 0032).

## Development Workflow

### Deployment

Everything deploys from `ansible/` via `make`: `make all` for the whole platform, or one
`make install-<component>` target per logical component (see `ansible/Makefile` for the
list). Roles are idempotent and safe to re-run. Dry-run a change with
`ANSIBLE_CMD="--check --diff" make install-<component>`.

Prerequisites: Ansible 2.14+, SSH to the target nodes, kubectl pointed at the cluster.
ADR 0031 replaces these targets with Flux at the GitOps cutover.

### Configuration

1. `cp ansible/inventory.example.yaml ansible/inventory.yaml`
2. Edit `inventory.yaml`: hosts, storage paths, secrets (Ansible Vault), resource
   allocations, feature flags, namespace overrides. **Site-specific config belongs here,
   not in role defaults.**
3. `make all` or an individual `make install-*`.

Precedence, lowest to highest: role defaults → `roles/scout_common/defaults/main.yaml` →
`inventory.yaml` → `group_vars/all/versions.yaml` → `-e` extra vars. Note the
non-obvious one: **versions in `group_vars` outrank `inventory.yaml`**, so test a
different version with `-e`, and change pinned versions in `versions.yaml`.

Verify with `ansible-inventory -i inventory.yaml --list` (or `--host <hostname>`).

### Feature Flags

Optional components are off by default and enabled in `inventory.yaml`. Each requires
storage paths and vault secrets; the deploy asserts the required secrets are non-empty,
and the role README is the authoritative list.

- `enable_chat` — Open WebUI + Ollama. GPU node recommended; needs post-deployment setup
  (`ansible/roles/open-webui/README.md`).
- `enable_xnat` — XNAT imaging platform (`ansible/roles/xnat/README.md`, ADR 0026). When
  false, nothing XNAT is created, including its Keycloak client — so toggling back to
  false orphans provisioned XNAT users.

### Local Development

- Java/Gradle (`extractor/hl7log-extractor`, `hl7-listener`): `./gradlew build`
- Python (`extractor/hl7-transformer`, `report-viewer`, `sdk/python`): `pytest`
- `launchpad`: `npm install && npm run dev`

Pre-commit hooks are documented in `docs/internal/precommit.md`; Ansible role tests use
Molecule (`docs/internal/molecule_ansible_testing.md`).

## Ingestion

`IngestHl7LogWorkflow` (Temporal, task queue `ingest-hl7-log`) finds log files, splits and
uploads messages to bronze, then transforms them into the silver `reports` table. Omitted
input parameters fall back to Ansible inventory defaults; the input model is
`extractor/hl7log-extractor/.../model/IngestHl7LogWorkflowInput.java`, and
`docs/source/ingest.md` documents the workflow for operators.

```bash
kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"logsRootPath": "/data/hl7", "reportTableName": "reports"}'
```

Re-ingesting unchanged content is a no-op by design (ADR 0032).

## Querying the Lake

Everything user-facing reads through Trino (`delta.default.reports`), so per-user row
filters and column masks always apply. From notebooks or Python:

```python
import scout
df = scout.query("SELECT * FROM reports WHERE modality = :m", params={"m": "MRI"})
```

`scout.query()` returns a pandas DataFrame; `scout.connect()` returns a Trino DB-API
connection for streaming large results. Gotchas worth knowing before writing SQL:

- Filter array-of-struct columns with `any_match()`:
  `WHERE any_match(diagnoses, x -> x.diagnosis_code = 'J18.9')`.
- To match a column against a list parameter use `contains(:vals, col)`, **not** `IN` —
  the SQLAlchemy dialect does not expand list params into `IN` clauses.
- Filter on `year` where possible; it is the partition column.

## Monitoring

Grafana holds the dashboards (under **Dashboards > Scout**), Prometheus the metrics, Loki
the logs from every service. Provisioned dashboards live in
`ansible/roles/grafana/templates/dashboards/` — edit in the UI, then export the JSON into
that directory (`docs/internal/grafana-dashboards-and-alerts.md`). Workflow-level ingest
detail is in the Temporal UI.

## Troubleshooting

```bash
kubectl get pods -A                          # what's unhealthy
kubectl logs -n <namespace> <pod> [-f]       # service logs (also in Loki)
kubectl describe pod -n <namespace> <pod>    # events, scheduling failures
```

Then: Grafana dashboards for metrics, Grafana > Explore > Loki for aggregated logs, the
Temporal UI for workflow execution detail.

## Testing

- Python unit tests: `pytest` in `extractor/hl7-transformer`, `report-viewer`, `sdk/python`
- Java: `./gradlew test` in `extractor/hl7log-extractor`, `hl7-listener`
- Integration/e2e under `tests/`: `ingest/` (Gradle, end-to-end Temporal ingestion),
  `auth/` (Playwright, oauth2-proxy + Keycloak), `data-authorization/` (in-cluster Job,
  the Keycloak → OPA → Trino AuthZ pipeline), `network/` (NetworkPolicy checks). See
  `docs/internal/integration_tests.md`.

## Air-Gapped Deployment

Set `air_gapped: true` and define a `staging` group in `inventory.yaml`; deploy the
staging node (`make install-staging`), then `make all`. The staging node runs Harbor as a
pull-through image proxy, Nexus for packages (conda/PyPI/Maven/RPM), and Squid for
allowlisted outbound OAuth. See `ansible/README.md`,
`docs/internal/staging-node-architecture.md`, and ADRs 0001, 0002, 0007, 0016–0018.

## Custom Ansible Filter Plugins

In `ansible/filter_plugins/` — see `ansible/README.md` for the full set and tests.

- `jvm_memory_to_k8s` — JVM decimal heap size → K8s binary, optional multiplier:
  `"{{ cassandra_max_heap | jvm_memory_to_k8s(2) }}"` turns `2G` into `4Gi`
- `multiply_memory` — scales a value while keeping decimal units (for configs that must
  not be binary)

## CI, Versioning, and Release Conventions

Working rules for changes that touch CI, releases, or published artifacts. The design and
rationale are in ADRs 0030 and 0031.

- **PR titles must be Conventional Commits** (`feat`, `fix`, `chore`, `ci`, `docs`,
  `refactor`, `test`, `perf`, `build`, `revert`; trailing `!` = breaking). The
  `PR Title Lint` check enforces it, and release automation reads the title for the
  version bump and changelog. Prefer `fix(scope):` over a bare custom type.
- **`main` takes squash merges only.** Release automation and the build lane's
  run-number ordering both depend on the linear history.
- **Do not hand-edit version fields.** `Chart.yaml`, `VERSION`, and `pyproject.toml`
  versions are placeholders stamped at publish time; release versions come from
  release-please, not from you.
- **Never enable registry auto-pruning** (delete-untagged, older-than-N). Content is
  pinned by digest under possibly-old tags, so pruning would reap live content.
- **The `changes` job in `.github/workflows/ci.yaml` is the single path → component map.**
  Adding an image or chart means adding its `dorny/paths-filter` entry and output there
  (plus, for a new image, an `&image-matrix` entry and a `<subproject>/.trivyignore.yaml`).
- **`scan-images` blocks on fixable HIGH/CRITICAL CVEs** left after a per-image
  `.trivyignore.yaml`. Fix the dependency where we own it; suppress with a documented
  reason only what an upstream base image bundles.

CI helper code lives in `.github/scripts/` and `tooling/`; reusable steps in
`.github/actions/`. Release mechanics and the full per-release checklist are in
`docs/internal/versions-and-releases.md`.

## Architecture Decision Records (ADRs)

ADRs live in `docs/internal/adr/` and are authoritative for the areas they cover. The
list below is for routing only — it tells you which file to open, not what it says. Read
the ADR itself before changing anything it covers.

- **0001** air-gapped Helm — installs go out over kubeconfig, not an OCI cache or local render
- **0002** air-gapped K3s — images via Harbor pull-through (SELinux RPM part superseded by 0017)
- **0003** oauth2-proxy — gates at ingress; services keep their own Keycloak clients. Read when adding a protected service
- **0004** storage provisioning — dynamic provisioning + storage classes, optional multidisk (Jupyter parts superseded by 0006)
- **0005** MinIO credentials — why static keys, not STS (S3A can't use a custom STS endpoint)
- **0006** Jupyter placement — pinned to GPU nodes, local storage only (SQLite locking breaks on NFS)
- **0007** jump node — Ansible control node kept separate from the staging node in air-gapped deployments
- **0008** Ollama models — pre-staged to NFS from staging, mounted read-only in prod
- **0009** Open WebUI CSP — Traefik middleware against LLM-driven browser exfiltration
- **0010** Open WebUI link filter — sanitizes external URLs mid-stream, covering what CSP can't
- **0011** layered deployment — three layers + service-mode vars (`postgres_mode`, `redis_mode`, …). Read when adding a service
- **0012** security hardening — global Traefik security-headers middleware; responses to scan findings
- **0013** Valkey — standalone Valkey is `redis_mode: standalone`, the default
- **0014** OWUI summarization filter — **superseded** by native context compaction; historical only
- **0015** dependency CVE monitoring — Renovate watches `versions.yaml` (new entries need a `# renovate:` annotation), Dependabot watches app deps
- **0016** staging cert — the staging self-signed cert is trusted explicitly, not skipped
- **0017** package proxy — Nexus proxies conda/PyPI/Maven/RPM via per-format `*_proxy_url` vars
- **0018** Squid — domain-allowlisted forward proxy so air-gapped Keycloak can reach external IdPs
- **0019** `trino-rw` — a write-capable Trino that exists only for transformer view DDL; user-facing Trino stays read-only
- **0020** Trino AuthZ — OPA + Keycloak attributes; a new restriction dimension is one `trino_attribute_filters` edit. Policy: `policy/trino/main.rego`
- **0021** OPA user data — a Keycloak SPI publishes user-attribute bundles to MinIO; OPA pulls them into `data.users`
- **0022** Trino auth — JWT on Trino; Jupyter passes the user's token through, other clients impersonate via `X-Trino-User`. Read before touching any client's Trino auth
- **0023** view security — the `_epic_view` family is SECURITY DEFINER; OPA carves out view owners and hidden tables
- **0024** SDK token refresh — how short-lived bearers stay valid in notebooks. Read when debugging notebook 401s / `JWT expired`
- **0025** user admin — administration lives in the launchpad `/admin/users` console over a `scout-users` Keycloak SPI resource
- **0026** XNAT posture — what `enable_xnat` creates: secrets, non-interactive first boot, SSO-only login, storage/DB/mail, destructive disable
- **0027** XNAT plugins — **superseded**: plugins install chart-natively; the `xnat-plugin-installer` image is gone
- **0028** HL7 listener — Camel MLLP listener + Kafka batcher, **observer mode only**; nothing downstream consumes `hl7-raw` yet
- **0029** report-viewer — FastAPI + React cohort browser for chat; replaced Open WebUI's MCP Trino tool
- **0030** versioning — build lane `0.YYYYMMDD.<run>` + signed build manifest, release lane `X.Y.Z` from PR titles. Working rules are in the CI section above
- **0031** GitOps — Flux consumes `deploy/`, Ansible shrinks to bootstrap. Read before changing any component's deployment
- **0032** re-ingest gate — `content_hash` makes an unchanged re-ingest a no-op. Read before touching the base merge or OBX ordering
- **0033** Hauler build lane — the build manifest is a signed Hauler haul. Read before changing build-lane bundling or air-gap transport

For 0030/0031 start with `docs/internal/adr/0030-0031-tldr.md`; the phased migration plan
is `docs/internal/gitops-implementation-plan.md`.

**When you add an ADR, add exactly one line here**: the decision in a clause, plus the
trigger that should send a reader to the file. This is a routing table, not a set of
summaries — an agent decides from this line whether to open the ADR, and gets every
detail from the ADR. Do not restate config keys, variable names, thresholds, or
rationale; duplicated detail goes stale silently and crowds out the rest of this file. If
a line is growing past one sentence, that is a sign the ADR should be read instead.

## Common Modification Patterns

- **Add an HL7 field** — parser in `extractor/hl7-transformer/`, then
  `docs/source/dataschema.md`, then the "Tables & Columns Reference" in
  `helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md` (schema docs are
  inlined into that prompt because native function-calling bypasses OWUI's RAG
  injection, so a new field is invisible to chat until it is added there).
- **Change the ingest workflow** — Java in `extractor/hl7log-extractor/`, then
  `make install-extractor`.
- **Adjust resources (heap, CPU, memory, storage)** — override in `inventory.yaml`.
- **Add a Superset dashboard, chart, or dataset** — export the asset YAML into
  `helm/scout-dashboards/files/analytics/<charts|dashboards|datasets/Scout_Data_Lake>/<bundle>/`; a new
  bundle also needs its name in `scout_dashboard_bundles` in inventory. See
  `helm/scout-dashboards/README.md`.
- **Update a dependency version** — `ansible/group_vars/all/versions.yaml`, with a
  `# renovate:` annotation so CVE monitoring picks it up (ADR 0015), then redeploy.
- **Add a CI-built image or service** — wiring `.github/workflows/ci.yaml` only covers
  `main`. The release path must be wired too (`.github/scripts/update-versions.sh`, the
  `IMAGES=` list in `.github/workflows/release.yaml`, and the tables in
  `docs/internal/versions-and-releases.md`), or a tagged release ships the image frozen
  at its last `main` build.
- **Write Ansible tasks using `kubernetes.core`** — follow the kubeconfig conventions in
  `docs/internal/ansible_roles.md` (they differ for cluster vs jump-node execution).

## License

See `LICENSE`.
