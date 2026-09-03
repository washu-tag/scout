# Required secrets (ADR 0031)

The `deploy/` base references Kubernetes Secrets by **fixed name only**, never their
values. A site materializes each one; how depends on the deployment mode:

- **Cloud** (AWS estates): the site's IaC (Terraform) writes the backing values to a
  secrets manager (AWS Secrets Manager under a site-chosen prefix, e.g. `/scout/*`),
  and External Secrets Operator pulls them in via a `ClusterSecretStore`. Values are
  never in git.
- **Air-gapped / on-prem**: SOPS-encrypted Secrets committed to the site repo,
  decrypted by Flux's kustomize-controller (ADR 0031 §3).

Names and keys are the same in both modes; only materialization differs, **except the
object-store credentials**, which are mode-specific (see `scout-data` below). This is
the secret analog of `required-vars.txt`. Namespaces below are the base's logical ones
(`${scout_*_namespace}` etc.), resolved per site.

## scout-core (postgres / keycloak / valkey)
| secret | keys | consumed by |
| --- | --- | --- |
| `superuser-secret` | `username`, `password` | CNPG `Cluster.superuserSecret` |
| `cnpg-role-{hive,hive-readonly,keycloak,superset,extractor,temporal}` | `username`, `password` | CNPG managed roles |
| `keycloak-db-secret` | `username`, `password` | Keycloak CR datasource (= the keycloak role) |
| `keycloak-admin-secret` | `username`, `password` | Keycloak bootstrap admin + config-cli |
| `keycloak-client-secrets` | `oauth2_proxy`, `superset`, `superset_svc`, `jupyterhub`, `grafana`, `temporal`, `launchpad_client`, `minio`, `open_webui`, `voila_svc`, `report_viewer_svc`; `github_client_id`/`github_client_secret` (when `github.enabled`); `microsoft_client_id`/`microsoft_client_secret`/`microsoft_tenant_id` (when `microsoft.enabled`); `xnat` (when `enableXnat`) | config-cli realm import (`envFrom`; keys are the `$(env:...)` var-substitution names) |
| `valkey-auth` | `password`, `password-file` | Valkey chart + exporter |
| `launchpad-keycloak-secret` | `client-secret` | launchpad OIDC login (pod-side; = the realm's `launchpad_client` value, not that key) |
| `launchpad-nextauth-secret` | `secret` | launchpad next-auth session signing (generate-once) |

## scout-data (minio / hive)
**Mode-specific.** Cloud uses AWS S3 + IRSA (no access-key Secrets); the MinIO-user
credential Secrets below exist only when the base runs its **in-cluster MinIO** (the
air-gapped storage mode). The cloud/air-gapped storage flip is tracked separately.

| secret | keys | consumed by |
| --- | --- | --- |
| `hive-metastore-secret` / `-readonly-secret` | `S3_SECRET_KEY`, `HIVE_METASTORE_PASSWORD` | hive-metastore Deployments |
| `minio-scout-env-configuration` | `config.env` (root creds + region/OIDC) | MinIO `Tenant.configSecret` (in-cluster MinIO only) |
| `${s3_*}-creds` (lake r/w, loki-writer, opa-bundle r/w) | `CONSOLE_ACCESS_KEY`, `CONSOLE_SECRET_KEY` | MinIO `Tenant.users` (in-cluster MinIO only) |

## scout-extractor (extractor / trino-rw)
| secret | keys | consumed by |
| --- | --- | --- |
| `s3-secret` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | hl7log-extractor + hl7-transformer (lake-writer; cloud = IRSA instead) |
| `postgres-secret` | `DB_PASSWORD` (+ DB coords) | extractor datasource (= the extractor role) |
| `temporal-db-secret` | `password` | Temporal server + schema Job (= the temporal CNPG role) |
| `trino-rw-s3` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | trino-rw (lake-writer; cloud = IRSA instead) |

## scout-analytics (superset / opa / trino-ro)
| secret | keys | consumed by |
| --- | --- | --- |
| `trino-s3` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | trino-ro (lake-reader; cloud = IRSA instead) |
| `trino-authz-env` | `KEYSTORE_PASSWORD`, `INTERNAL_SHARED_SECRET` | trino-ro + cert-manager (generate-once) |
| `superset-env` | DB + Redis + OIDC client secrets + `SUPERSET_SECRET_KEY` | superset server + dashboards |
| `opa-bundle-reader` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | scout-opa bundle reader |

## kube-system (oauth2-proxy)
| secret | keys | consumed by |
| --- | --- | --- |
| `oauth2-proxy` | `client-id`, `client-secret`, `cookie-secret` | oauth2-proxy (cookie-secret generate-once) |
| `oauth2-proxy-redis` | `redis-password` | oauth2-proxy session store (= valkey password) |
| `oauth2-proxy-logo` | `logo.png` | oauth2-proxy sign-in page, **non-optional** volume mount |

`oauth2-proxy-logo` is a static asset (the ~158 KB sign-in logo), not a credential: the
base does not ship or generate it (unlike the `oauth2-proxy-templates` ConfigMap), so a
site must provide it as a Secret (from CI or the site repo, not the secrets manager) or
oauth2-proxy stays in `ContainerCreating` on the missing mount.

## Not site-provided (generated in-cluster, listed so they aren't double-provisioned)
- `trino-tls` — cert-manager `Certificate`
- `superset-config` — rendered config (CI / chart), not credentials

## Notes for cloud setups
- Provision the backing values with your IaC; keep them out of git. A typical AWS estate
  does this with Terraform into AWS Secrets Manager, consumed by ESO.
- **Data tier is managed in aws (ADR 0036).** postgres = RDS: no CNPG deploy
  (`postgres-ready` is an inert marker), consumers reach it via the `${postgres_host}` site
  var, and the DB credential Secrets are materialized from RDS (same fixed names) instead of
  the operator. Temporal runs on Postgres (no Cassandra/Elasticsearch), so its history +
  visibility databases live in RDS too.
- Several values are shared across secrets (e.g. one Postgres role password appears in
  its `cnpg-role-*` and in the app's DB secret; one lake credential appears under
  several key names). Provision the value once and template it into each Secret.
- Generate-once values with no natural source (`SUPERSET_SECRET_KEY`, the oauth2-proxy
  `cookie-secret`, `trino-authz-env`, the launchpad `launchpad-nextauth-secret`) should
  be created once and stored, not rotated casually (some are consumed at TLS-issue time).
- Rotating a `keycloak-client-secrets` value does not by itself re-run the config-cli
  import (the Job reads it via `envFrom` by name); it applies on the next realm/chart
  upgrade, or force it with `flux reconcile hr keycloak-config-cli -n <ns>`.
- Every enabled component's key must be present. config-cli leaves an unresolved
  `$(env:...)` as literal text, so a missing key would set that client's secret to a
  guessable placeholder. Provision `keycloak-client-secrets` fail-closed (an
  ExternalSecret that errors if a source key is absent), and only enable an IdP or the
  XNAT client once its key exists.
