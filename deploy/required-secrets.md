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
| `keycloak-db-secret` | `username`, `password` | Keycloak CR datasource (= the keycloak role; `keycloak_iam` under [RDS IAM auth](#rds-iam-database-auth-aws-opt-in)) |
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
| `hive-metastore-secret` / `-readonly-secret` | `S3_SECRET_KEY`, `HIVE_METASTORE_PASSWORD` (ignored but must be non-empty under `hive_db_auth: iam`) | hive-metastore Deployments |
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
| `superset-env` | DB + Redis + OIDC client secrets + `SUPERSET_SECRET_KEY`; RDS IAM auth: `DB_IAM_AUTH=true`, `DB_USER` = the IAM login role, optional `DB_SSLMODE` / `DB_SSLROOTCERT` (see below) | superset server + dashboards |
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
An aws site can move Postgres clients to passwordless RDS IAM auth, one component at a
time. Nothing in the base turns it on: a client keeps its password login until the site
flips it as below.

- **Login roles.** For each owner role `R` whose client flips, create a separate login role
  `R_iam` with no password, and point the client's username at it:
  `CREATE ROLE R_iam LOGIN; GRANT R TO R_iam; GRANT rds_iam TO R_iam; ALTER ROLE R_iam SET role = 'R';`.
  Sessions then act as `R`, so objects stay owned by `R`. `R` keeps its password during the
  cutover, so rollback is switching the client's username back to `R`.
- **Never grant `rds_iam` to a role the RDS master user is a member of** (the owner roles,
  typically), and never make the master a member of an `R_iam`. RDS makes any login role
  that reaches `rds_iam` through membership IAM-only, so the master would lose its password
  login. `SELECT pg_has_role('<master>', 'rds_iam', 'MEMBER')` must stay false.
- **TLS.** RDS accepts IAM tokens over TLS only. Clients use `sslmode=verify-full` against a
  site-provided ConfigMap `rds-ca-bundle` (key `ca.pem`: the regional RDS bundle for the
  instance's region, e.g. `https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem`)
  in each namespace with a flipped client, mounted at `/etc/rds-ca`.
- **IRSA.** The client's ServiceAccount role needs `rds-db:connect` on
  `arn:aws:rds-db:<region>:<account>:dbuser:<DbiResourceId>/R_iam`.

| client | login role | ServiceAccount (namespace) | flip |
| --- | --- | --- | --- |
| Keycloak | `keycloak_iam` | `keycloak-opa-bundle-writer` (`${keycloak_namespace}`) | `keycloak-db-secret` `username: keycloak_iam` + the CR patch below |

### Keycloak
The scout keycloak image ships the AWS Advanced JDBC Wrapper and the SDK `rds` module in
`providers/`, unused unless `db-driver` selects the wrapper. `db-driver` is a build-time
option, so the CR also needs `startOptimized: false`: Keycloak re-augments at each start
(a few seconds; `/opt/keycloak/lib` must stay writable, as in the stock image). Left on
`--optimized`, a changed `db-driver` makes Keycloak exit at startup. Append this JSON6902
patch to the `keycloak-instance` Flux Kustomization's `spec.patches`:

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
      value: [{name: rds-ca, configMap: {name: rds-ca-bundle}}]
    - op: add
      path: /spec/unsupported/podTemplate/spec/containers
      value: [{name: keycloak, volumeMounts: [{name: rds-ca, mountPath: /etc/rds-ca, readOnly: true}]}]
```

- `db.url` overrides the CR's host/port/database. `${postgres_host}` resolves in that
  Kustomization's postBuild (write `$${postgres_host}` if the manifest carrying the patch is
  itself substituted). The wrapper takes the token's region from the RDS hostname; set
  `iamHost`/`iamRegion` only when connecting through a CNAME.
- Keep `wrapperPlugins=iam` explicit: the wrapper's default plugins target Aurora failover and
  open extra monitoring connections (on Aurora add `failover2`, as Keycloak's docs advise).
- The pod runs as `keycloak-opa-bundle-writer`, so the `rds-db:connect` grant for
  `keycloak_iam` goes on that SA's role (`${irsa_role_prefix}-opa-bundle-writer`), next to its
  OPA-bundle S3 grant. The wrapper uses the SDK default credential chain (web identity).
- Land the `username: keycloak_iam` flip and the patch together; either one alone fails the
  login. The `password` key in `keycloak-db-secret` goes unused; keep it until the cutover
  soaks, so rollback is dropping the patch and restoring `username: keycloak`.
- To stage the flip, first apply the patch with `wrapperPlugins=` (empty), without the
  `passwordSecret` removal and with `username: keycloak`. That proves the driver swap,
  re-augmentation and verify-full TLS on password auth before switching to IAM.
Passwordless Postgres logins for the components below, each off by default behind its
own cluster-var. Turning one on needs, per component:

- **An IAM login role** next to the owner role `R`, so objects stay owned by `R` and
  the password login still works for rollback:
  ```sql
  CREATE ROLE R_iam LOGIN;               -- no password
  GRANT R TO R_iam;
  GRANT rds_iam TO R_iam;
  ALTER ROLE R_iam SET role = 'R';       -- sessions act as R
  ```
  **Never grant `rds_iam` to an owner role, or to any role the master user is a member
  of.** RDS honours nested membership, so the master would become IAM-only and its
  password login would be refused. Keep the master out of every `R_iam`, and since
  Postgres 16+ makes a non-superuser creator a member of each role it creates, verify
  rather than assume: `pg_has_role('<master>', 'rds_iam', 'MEMBER')` must be false.
- **An IRSA role** `${irsa_role_prefix}-<suffix>` trusting the ServiceAccount below,
  allowed `rds-db:connect` on `arn:aws:rds-db:<region>:<account>:dbuser:<DbiResourceId>/R_iam`.
- **A `rds-ca-bundle` ConfigMap** (key `ca.pem`, the RDS CA bundle for the instance's
  region, e.g. `https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem`)
  in the component's namespace, mounted at `/etc/rds-ca`. IAM auth requires TLS; clients
  use `sslmode=verify-full`. The mount is optional, so a missing ConfigMap surfaces as a
  connection error, not a stuck pod.

| component | cluster-var | ServiceAccount (namespace) | IRSA suffix | login role | switch |
| --- | --- | --- | --- | --- | --- |
| superset (server, worker, init Job, dashboards Job) | `superset_db_auth: iam` (default `password`) | `superset` (`${scout_analytics_namespace}`) | `-superset` | `superset_iam` | `superset-env`: `DB_IAM_AUTH=true` + `DB_USER=superset_iam` |

Superset: `superset_db_auth=iam` only adds the ServiceAccount, the CA mount and the
token hook, which stays inert until `superset-env` flips. Change `DB_IAM_AUTH` and
`DB_USER` together, then restart `superset` and `superset-worker` (pods don't roll on a
Secret change by themselves); the init and dashboards Jobs pick it up on the next
upgrade. Roll back by reverting those two keys. `DB_SSLMODE` (default `verify-full`) and
`DB_SSLROOTCERT` (default `/etc/rds-ca/ca.pem`) override the TLS settings. Tokens are
minted in `AWS_REGION` (set by the EKS pod-identity webhook), else `AWS_DEFAULT_REGION`.
A site on RDS can switch a component from password to IAM database auth, one at a time.
Each switch is a cluster-var that defaults to password, so nothing changes until a site
sets it. The component then connects as a separate IAM login role over TLS
(`sslmode=verify-full`), using a short-lived token minted from its IRSA identity.

- **Login roles.** Keep each owner role (`hive`, `hive_readonly`, ...) and its password
  unchanged, and add a login role that acts as it, for example:
  `CREATE ROLE hive_iam LOGIN; GRANT hive TO hive_iam; GRANT rds_iam TO hive_iam;
  ALTER ROLE hive_iam SET role = 'hive';`. Sessions then run as, and objects stay owned
  by, `hive`. To roll back, set the component back to password auth and the username
  back to the owner. Keeping the owner's password in the Secret means rollback needs no
  Secret change.
- **Never grant `rds_iam` to a role the master user is a member of.** Membership counts
  even when it's indirect, and it makes that login IAM-only, so the master (and
  `hive-db-init`, which runs as `superuser-secret` over a password) would be locked out.
  Grant `rds_iam` only to the `*_iam` login roles, and never make the master a member of
  one.
- **CA bundle.** Provide a ConfigMap `rds-ca-bundle` with key `ca.pem`, holding the RDS
  CA bundle for your region (`https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem`),
  in each namespace whose components use IAM. It's mounted at `/etc/rds-ca`.
- **IRSA.** Each workload's role needs `rds-db:connect` on
  `arn:aws:rds-db:<region>:<account>:dbuser:<DbiResourceId>/<login role>`.

hive (`${hive_namespace}`):

| cluster-var | default | iam value |
| --- | --- | --- |
| `hive_db_auth` | `password` | `iam` (the `hive-metastore` image with the AWS JDBC wrapper, and the `jdbc:aws-wrapper` URL) |
| `hive_db_user` | `hive` | the write login role, e.g. `hive_iam` |
| `hive_readonly_db_user` | `hive_readonly` | the readonly login role, e.g. `hive_readonly_iam` |

The write metastore's ServiceAccount `hive-metastore` uses `${irsa_role_prefix}-hive-metastore`,
and the readonly one's (`hive-metastore-readonly`) uses its own, in both auth modes:
`${irsa_role_prefix}-hive-metastore-readonly`. That role needs lake read access and
`rds-db:connect` for the readonly login role only, and its trust must admit
`system:serviceaccount:${hive_namespace}:hive-metastore-readonly`. `hive-db-init` is
unchanged: it keeps running as the master over a password.
