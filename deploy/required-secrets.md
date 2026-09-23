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
| `keycloak-db-secret` | `username`, `password` | Keycloak CR datasource (= the keycloak role; its IAM login role under [RDS IAM auth](#rds-iam-database-auth-aws-opt-in)) |
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
| `hive-metastore-secret` / `-readonly-secret` | `S3_SECRET_KEY`, `HIVE_METASTORE_PASSWORD` (still non-empty under `hive_db_auth: iam`, where the token replaces it) | hive-metastore Deployments |
| `minio-scout-env-configuration` | `config.env` (root creds + region/OIDC) | MinIO `Tenant.configSecret` (in-cluster MinIO only) |
| `${s3_*}-creds` (lake r/w, loki-writer, opa-bundle r/w) | `CONSOLE_ACCESS_KEY`, `CONSOLE_SECRET_KEY` | MinIO `Tenant.users` (in-cluster MinIO only) |

## scout-extractor (extractor / trino-rw)
| secret | keys | consumed by |
| --- | --- | --- |
| `s3-secret` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | hl7log-extractor + hl7-transformer (lake-writer; cloud = IRSA instead) |
| `postgres-secret` | `DB_PASSWORD` (+ DB coords); optional [RDS IAM auth](#rds-iam-database-auth-aws-opt-in) keys | extractor datasource (= the extractor role) |
| `temporal-db-secret` | `password` | Temporal server + schema Job (= the temporal CNPG role); only the schema step under `temporal_db_auth: iam` |
| `trino-rw-s3` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | trino-rw (lake-writer; cloud = IRSA instead) |

## scout-analytics (superset / opa / trino-ro)
| secret | keys | consumed by |
| --- | --- | --- |
| `trino-s3` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | trino-ro (lake-reader; cloud = IRSA instead) |
| `trino-authz-env` | `KEYSTORE_PASSWORD`, `INTERNAL_SHARED_SECRET` | trino-ro + cert-manager (generate-once) |
| `superset-env` | DB + Redis + OIDC client secrets + `SUPERSET_SECRET_KEY`; optional [RDS IAM auth](#rds-iam-database-auth-aws-opt-in) keys | superset server + dashboards |
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

## RDS IAM database auth (aws, opt-in)
An aws site whose Postgres is RDS can move clients to passwordless IAM database auth, one
component at a time. Nothing changes until the site opts in: each cluster-var below
defaults to password auth, and the Secret keys are optional. A switched client logs in as
a separate IAM login role with a short-lived token minted from its IRSA identity, over
verified TLS.

What a site provides before it switches a component (all inert until then):

- **An IAM login role per owner role `R`.** Keep `R` and its password, so objects stay
  owned by `R` and rollback is switching the client's username back:
  ```sql
  BEGIN;
  CREATE ROLE R_iam LOGIN;           -- no password
  GRANT R TO R_iam;
  ALTER ROLE R_iam SET role = 'R';   -- sessions act as R
  GRANT rds_iam TO R_iam;
  DO $$ BEGIN
    IF pg_has_role('<master>', 'rds_iam', 'MEMBER') THEN
      RAISE EXCEPTION 'the master would reach rds_iam';
    END IF;
  END $$;
  COMMIT;                            -- after a failed check this rolls back
  ```
- **Never grant `rds_iam` to a role the master user is a member of** (typically the owner
  roles), and never make the master a member of an `R_iam`. RDS makes any login that
  reaches `rds_iam`, even through nested membership, IAM-only, so the master would lose its
  password login (and `hive-db-init`, which runs as the master, would fail). On PostgreSQL
  16+ the role that runs `CREATE ROLE` becomes a member of the new role, which the check
  catches.
- **A ConfigMap `rds-ca-bundle`** (key `ca.pem`: the RDS CA bundle for the instance's
  region, `https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem`) in each
  namespace with a switched client. It is mounted (optional) at `/etc/rds-ca` and clients
  use `sslmode=verify-full` against `/etc/rds-ca/ca.pem`, so `postgres_host` must be the
  instance endpoint. A missing ConfigMap shows up as a TLS error at connect time.
- **An IRSA role per workload** (table) that trusts
  `system:serviceaccount:<namespace>:<ServiceAccount>` and allows `rds-db:connect` on
  `arn:aws:rds-db:<region>:<account>:dbuser:<DbiResourceId>/<login role>`. The Python
  clients and the Temporal sidecar mint tokens in `AWS_REGION` (set by the EKS pod identity
  webhook), else `AWS_DEFAULT_REGION`; the Java clients (AWS Advanced JDBC Wrapper, with
  `wrapperPlugins=iam` kept explicit) take it from the RDS hostname.

| component (namespace) | cluster-vars (default) | ServiceAccount / IRSA role | login role | switch |
| --- | --- | --- | --- | --- |
| Keycloak (`${keycloak_namespace}`) | none | `keycloak-opa-bundle-writer` / `${irsa_role_prefix}-opa-bundle-writer` | `keycloak_iam` | `keycloak-db-secret` `username` + the CR patch below |
| Superset: server, worker, init-db + dashboards Jobs (`${scout_analytics_namespace}`) | `superset_db_auth` (`password`) | `superset` / `${irsa_role_prefix}-superset` | `superset_iam` | `superset-env`: `DB_IAM_AUTH=true` + `DB_USER` |
| hive-metastore (`${hive_namespace}`) | `hive_db_auth` (`password`), `hive_db_user` (`hive`) | `hive-metastore` / `${irsa_role_prefix}-hive-metastore` | `hive_iam` | the cluster-vars |
| hive-metastore-readonly (`${hive_namespace}`) | `hive_db_auth`, `hive_readonly_db_user` (`hive_readonly`) | `hive-metastore-readonly` / `${irsa_role_prefix}-hive-metastore-readonly` | `hive_readonly_iam` | the cluster-vars |
| hl7log-extractor (`${scout_extractor_namespace}`) | `extractor_db_auth` (`password`) | `hl7log-extractor` / `${irsa_role_prefix}-hl7log-extractor` | `<postgres_user>_iam` | `postgres-secret`: `SPRING_DATASOURCE_URL`, `_DRIVERCLASSNAME`, `_USERNAME` |
| hl7-transformer (`${scout_extractor_namespace}`) | `extractor_db_auth` | `hl7-transformer` / `${irsa_role_prefix}-hl7-transformer` | `<postgres_user>_iam` | `postgres-secret`: `DB_IAM_AUTH=true` + `DB_USER` |
| Temporal: server + schema step (`${scout_extractor_namespace}`) | `temporal_db_auth` (`password`), `temporal_db_user` (`temporal`) | `temporal` / `${irsa_role_prefix}-temporal` | `temporal_iam` | the cluster-vars |

A `*_db_auth: iam` var switches hive and Temporal outright; for Superset and the extractor
workers it only prepares the pods (ServiceAccount, CA mount, token hook) and the Secret keys
switch them. Per component: IRSA role, login role and CA ConfigMap first, then the
cluster-vars, then the Secret keys. Pods don't restart on a Secret change, so restart the
workload after editing one. Roll back in reverse; the owner role's password still works.
Superset and hl7-transformer also read optional `DB_SSLMODE` (default `verify-full`) and
`DB_SSLROOTCERT` (default `/etc/rds-ca/ca.pem`) from their Secret. Keep `rds.force_ssl` off
until every client uses TLS: Temporal's password variant connects without it.

### Keycloak
The scout keycloak image ships the AWS Advanced JDBC Wrapper in `providers/`, unused unless
`db-driver` selects it. `db-driver` is a build-time option, so the CR also needs
`startOptimized: false` (Keycloak re-augments at each start, a few seconds); left on
`--optimized`, a changed `db-driver` makes Keycloak exit at startup. Append this JSON6902
patch to the `keycloak-instance` Flux Kustomization's `spec.patches`, in the same change as
`keycloak-db-secret` `username: keycloak_iam` (either one alone fails the login):

```yaml
- target: {kind: Keycloak, name: keycloak}
  patch: |
    - op: add
      path: /spec/startOptimized
      value: false
    - op: add
      path: /spec/db/url
      value: jdbc:aws-wrapper:postgresql://${postgres_host}:5432/keycloak?wrapperPlugins=iam&sslmode=verify-full&sslrootcert=/etc/rds-ca/ca.pem
    - op: remove
      path: /spec/db/passwordSecret
    - op: add
      path: /spec/additionalOptions/-
      value: {name: db-driver, value: software.amazon.jdbc.Driver}
    - op: add
      path: /spec/unsupported/podTemplate/spec/volumes
      value: [{name: rds-ca, configMap: {name: rds-ca-bundle, optional: true}}]
    - op: add
      path: /spec/unsupported/podTemplate/spec/containers
      value: [{name: keycloak, volumeMounts: [{name: rds-ca, mountPath: /etc/rds-ca, readOnly: true}]}]
```

- `db.url` overrides the CR's host/port/database. `${postgres_host}` resolves in that
  Kustomization's postBuild (write `$${postgres_host}` if the manifest carrying the patch is
  itself substituted). Set `iamHost`/`iamRegion` in the URL only when connecting through a
  CNAME.
- To stage it, apply the patch first with `wrapperPlugins=` (empty), without the
  `passwordSecret` removal and with `username: keycloak`. That proves the driver swap,
  re-augmentation and verify-full TLS on password auth. Roll back by dropping the patch and
  restoring `username: keycloak`.

### Superset
`superset_db_auth: iam` creates the `superset` ServiceAccount and runs the server, worker,
init-db Job and dashboards import Job as it, mounts the CA and loads a `do_connect` hook that
stays inert until `superset-env` sets `DB_IAM_AUTH=true`. Change `DB_IAM_AUTH` and
`DB_USER` together, then restart `superset` and `superset-worker`; the Jobs pick it up on
the next upgrade.

### Hive metastores
`hive_db_auth: iam` runs both metastores on `ghcr.io/washu-tag/hive-metastore` (the stock
image plus the AWS JDBC wrapper) with a `jdbc:aws-wrapper` URL; set the user vars in the
same change. `HIVE_METASTORE_PASSWORD` stays non-empty (the token replaces it), so rollback
is a cluster-var flip. The readonly metastore uses its own role,
`${irsa_role_prefix}-hive-metastore-readonly`, in both auth modes: create it (lake read,
plus `rds-db:connect` for the readonly login role) before upgrading. `hive-db-init` is
unchanged and keeps running as the master over a password.

### Extractor workers
`extractor_db_auth: iam` mounts the CA on both workers and lets `postgres-secret` override
hl7log-extractor's datasource. Each worker then switches on its own keys:
- hl7log-extractor: `SPRING_DATASOURCE_URL` =
  `jdbc:aws-wrapper:postgresql://<host>:<port>/<db>?wrapperPlugins=iam&sslmode=verify-full&sslrootcert=/etc/rds-ca/ca.pem`,
  `SPRING_DATASOURCE_DRIVERCLASSNAME` = `software.amazon.jdbc.Driver`,
  `SPRING_DATASOURCE_USERNAME` = the login role. Keep `DB_PASSWORD` present (ignored).
- hl7-transformer: `DB_IAM_AUTH=true` and `DB_USER` = the login role.

### Temporal
Set `temporal_db_auth: iam` and `temporal_db_user` together (a token for the owner role is
refused). The server pods then run as the `temporal` ServiceAccount (shipped in both modes,
unused under password) with a token sidecar (`public.ecr.aws/aws-cli/aws-cli`, pinned as
`aws_cli_image_tag`, not in the haul) whose file `passwordCommand` reads for each new
connection, over verify-full TLS. The chart's schema Job shares the stores' user, so the
schema step runs as the owner role with `temporal-db-secret` instead; keep that Secret, and
keep the owner's password login when retiring the other owners' passwords.
