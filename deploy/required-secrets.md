# Required secrets (ADR 0031)

The `deploy/` base references Kubernetes Secrets by **fixed name only**, never their
values. A site materializes each one; how depends on the deployment mode:

- **Cloud** (AWS estates): the site's IaC (Terraform) writes the backing values to a
  secrets manager (AWS Secrets Manager under a site-chosen prefix, e.g. `/scout/*`),
  and External Secrets Operator pulls them in via a `ClusterSecretStore`. Values are
  never in git.
- **Air-gapped / on-prem**: the artifact renders the Secrets below (all but the
  optional `keycloak-client-secrets-site`) from `base/secrets-on-prem`, filling them
  from **one** site Secret, `scout-secret-values` in `flux-system`, whose keys are listed
  in `required-secret-values.txt`. By default the site keeps it SOPS-encrypted in its
  repo and Flux's kustomize-controller decrypts it (ADR 0031 §3); another backend can
  write the same Secret if it applies the same value rules. See
  [On-prem: the values Secret](#on-prem-the-values-secret).

Names and keys are the same in both modes; only materialization differs, **except the
object-store credentials**, which are mode-specific (see `scout-data` below). This is
the secret analog of `required-vars.txt`. Namespaces below are the base's logical ones
(`${scout_*_namespace}` etc.), resolved per site.

## scout-core (postgres / keycloak / valkey)
| secret | keys | consumed by |
| --- | --- | --- |
| `superuser-secret` | `username`, `password` | CNPG `Cluster.superuserSecret` (on-prem only; `username` is exactly `postgres`) |
| `cnpg-role-{hive,hive-readonly,keycloak,superset,extractor,temporal}` | `username`, `password` | CNPG managed roles (on-prem only; `username` equals the role name: `hive`, `hive_readonly`, `keycloak`, `superset`, `${postgres_user}`, `temporal`) |
| `keycloak-db-secret` | `username`, `password` | Keycloak CR datasource (= the keycloak role) |
| `keycloak-admin-secret` | `username`, `password` | Keycloak bootstrap admin + config-cli |
| `keycloak-client-secrets` | `oauth2_proxy`, `superset`, `superset_svc`, `jupyterhub`, `grafana`, `temporal`, `launchpad_client`, `minio`, `open_webui`, `voila_svc`, `report_viewer_svc`, `fragment_reconciler_svc`; `github_client_id`/`github_client_secret` (when `github.enabled`); `microsoft_client_id`/`microsoft_client_secret`/`microsoft_tenant_id` (when `microsoft.enabled`); `xnat` (when `enableXnat`) | config-cli realm import (`envFrom`; keys are the `$(env:...)` var-substitution names). `fragment_reconciler_svc` is also read pod-side by the fragment reconciler, which is the one key with a second consumer |
| `keycloak-client-secrets-site` | any keys a site IdP document names (`deploy/README.md`) | config-cli realm import, **optional**. Read before `keycloak-client-secrets`, which wins on a name clash, so don't reuse its key names |
| `valkey-auth` | `password`, `password-file` | Valkey chart + exporter |
| `launchpad-keycloak-secret` | `client-secret` | launchpad OIDC login (pod-side; = the realm's `launchpad_client` value, not that key) |
| `launchpad-nextauth-secret` | `secret` | launchpad next-auth session signing (generate-once) |
| `opa-bundle-writer` | `access-key`, `secret-key` | Keycloak OPA bundle publisher (on-prem: MinIO creds; aws: present with **empty** values, else they shadow IRSA) |
| `alb-oidc-keycloak` | `clientID`, `clientSecret` | aws only: ALB-native OIDC on launchpad (also in `${scout_analytics_namespace}` for superset and `${scout_extractor_namespace}` for the Temporal UI); the `oauth2-proxy` client |

## scout-data (minio / hive)
**Mode-specific.** Cloud uses AWS S3 + IRSA (no access-key Secrets); the MinIO-user
credential Secrets below exist only when the base runs its **in-cluster MinIO** (the
air-gapped storage mode). The cloud/air-gapped storage flip is tracked separately.

| secret | keys | consumed by |
| --- | --- | --- |
| `hive-metastore-secret` / `-readonly-secret` | `S3_SECRET_KEY`, `HIVE_METASTORE_PASSWORD` | hive-metastore Deployments (aws: `S3_SECRET_KEY` present but empty) |
| `superuser-secret` | `username`, `password` | `hive-db-init` grant Job (both modes; aws = the RDS master) |
| `minio-scout-env-configuration` | `config.env` (root creds + region/OIDC) | MinIO `Tenant.configSecret` (in-cluster MinIO only) |
| `${s3_*}-creds` (lake r/w, loki-writer, opa-bundle r/w) | `CONSOLE_ACCESS_KEY`, `CONSOLE_SECRET_KEY` | MinIO `Tenant.users` (in-cluster MinIO only) |

## scout-extractor (extractor / temporal / trino-rw)
| secret | keys | consumed by |
| --- | --- | --- |
| `s3-secret` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | hl7log-extractor + hl7-transformer (lake-writer; cloud = IRSA instead) |
| `postgres-secret` | `DB_PASSWORD` (+ DB coords) | extractor datasource (= the extractor role) |
| `temporal-db-secret` | `password` | Temporal server + schema Job (= the temporal CNPG role) |
| `temporal-web-oidc` | `client-secret` | Temporal UI OIDC login, both modes (= the realm's `temporal` value in `keycloak-client-secrets`) |
| `alb-oidc-keycloak` | `clientID`, `clientSecret` | aws only: ALB-native OIDC on the Temporal UI (see scout-core) |
| `trino-rw-s3` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | trino-rw (lake-writer; cloud = IRSA instead) |

## scout-analytics (superset / opa / trino-ro)
| secret | keys | consumed by |
| --- | --- | --- |
| `trino-s3` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | trino-ro (lake-reader; cloud = IRSA instead) |
| `trino-authz-env` | `KEYSTORE_PASSWORD`, `INTERNAL_SHARED_SECRET` | trino-ro + cert-manager (generate-once) |
| `superset-env` | DB + Redis + OIDC client secrets + `SUPERSET_SECRET_KEY` | superset server + dashboards |
| `superset-valkey-auth` | `REDIS_PASSWORD` | scout-dashboards import Job |
| `opa-bundle-reader` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | scout-opa bundle reader (on-prem only; aws = IRSA) |
| `alb-oidc-keycloak` | `clientID`, `clientSecret` | aws only: ALB-native OIDC on superset (see scout-core) |

## kube-system (oauth2-proxy, on-prem only)
| secret | keys | consumed by |
| --- | --- | --- |
| `oauth2-proxy` | `client-id`, `client-secret`, `cookie-secret` | oauth2-proxy (cookie-secret generate-once) |
| `oauth2-proxy-redis` | `redis-password` | oauth2-proxy session store (= valkey password) |

## Not site-provided (generated in-cluster or shipped, listed so they aren't double-provisioned)
- `trino-tls`: cert-manager `Certificate`
- `superset-config`: rendered config (CI / chart), not credentials
- `oauth2-proxy-logo`: the sign-in logo, a `secretGenerator` in `base/oauth2-proxy`
- `${postgres_cluster_name}-{ca,server,replication,app}`: minted by the CNPG operator

## On-prem: the values Secret
`scout-secret-values` (Secret, `flux-system`) holds the keys in
`required-secret-values.txt`. Its annotations mark the optional MinIO settings
(`s3_username`, default `minio`; `minio_oidc_enabled`, default `on`, `off` where MinIO
can't trust Keycloak's certificate) and the conditional keys, set exactly while their
realm flag is `"true"`. The `secrets-ready` Kustomization substitutes it, with
`cluster-vars`, into the Secrets above; postgres, MinIO and valkey wait on it. Flux must
meet the on-prem floor in `deploy/README.md`, or a missing required key renders empty.

Generate it with `tooling/deploy/gen_secret_values.py --values <site values JSON>
--cluster-vars-values <the gen_cluster_vars.py --values file> -o <file>`, then encrypt
that owner-only file in place (e.g. `sops --encrypt --in-place --encrypted-regex
'^(data|stringData)$'`). The tool fails closed, never prints a value, and enforces these
rules, which keep a value intact through Flux and its consumers. Another backend must apply them too; running the tool's validation on
the values first is the simplest way.
- no `'`, no line break, control or format character, and no leading or trailing
  whitespace. The templates single-quote each value; Flux drops LF and folds CR to a
  space; CNPG and MinIO trim what the apps read untrimmed;
- the `keycloak-client-secrets` values use only `A-Z a-z 0-9 . _ ~ + / = -`, because
  config-cli substitutes them into the realm JSON before parsing it;
- `valkey_password` and `superset_postgres_password` use only `A-Z a-z 0-9 . _ ~ -`,
  because the superset chart builds connection URLs from them (and valkey's also sits
  in the exporter's `password-file` JSON);
- the `config.env` inputs contain no `"`, `$`, backtick or backslash (the file is
  double-quoted and sourced by sh);
- MinIO: `s3_password` and each `s3_*_secret` at least 8 characters, `s3_username` 3;
- `oauth2_proxy_cookie_secret` is 16, 24 or 32 bytes, raw or base64url-encoded;
- a conditional key is set exactly while its flag is on. The templates default these
  keys to empty, so strict substitution can't catch a flag turned on without its
  values: re-run the tool whenever a flag changes;
- `hive_namespace` differs from `postgres_cluster_namespace` (each gets a
  `superuser-secret`).

The tool labels the Secret `reconcile.fluxcd.io/watch: Enabled`, so an edit re-renders
the Secrets at once, and annotates it `kustomize.toolkit.fluxcd.io/substitute: disabled`,
so a site Kustomization with `postBuild` never expands `${...}` inside a value.

Keys the templates assemble from several inputs:
- `superset-env` also carries the non-secret DB and Redis coordinates, the service client
  ID, the token URL and `TRINO_CA_CERT`.
- `minio-scout-env-configuration` `config.env` carries the Ansible role's `export` lines,
  double-quoted, plus `MINIO_IDENTITY_OPENID_ENABLE_PRIMARY_IAM` from
  `minio_oidc_enabled` (Ansible omitted the OIDC lines instead).
- `valkey-auth` `password-file` is `{"redis://localhost:6379": "<valkey_password>"}`.

**Rotation.** Most values are re-applied on the next render, but on-prem has no
Reloader, so env consumers need a restart. CNPG re-sets role and superuser passwords
itself (`cnpg.io/reload`); a client secret needs a realm re-import (`flux reconcile hr
keycloak-config-cli -n <keycloak namespace> --force`) and an app restart; MinIO needs a
tenant restart and a `bootstrap-minio-iam` re-run. Three values are persisted and must
not change casually: `keycloak_bootstrap_admin_password` (change it in Keycloak first),
`superset_secret` (`superset re-encrypt-secrets` with the old key) and
`trino_keystore_password` (re-issue `trino-tls`).

**Adopting an Ansible site.** Copy each value from the live cluster Secrets and
cross-check the vault. Postgres may hold an older password than the vault, and CNPG
`ALTER`s every role to the seeded value on its first reconcile. A vault value made with
`encrypt_string` from a pipe ends in a newline, which the tool rejects: strip it, and
treat a stripped persisted value (above) as a rotation. Four Secrets were chart-owned
under Ansible (`launchpad-keycloak-secret`, `launchpad-nextauth-secret`, `superset-env`,
`oauth2-proxy`): their templates carry `helm.sh/resource-policy: keep`, and
`secrets-ready` reconciles before those releases, so Helm keeps them when Flux takes the
releases over. The base fixes the database role and database names, so rename any an
inventory renamed, and rotate a `valkey_password` or `superset_postgres_password`
outside the URL-safe set first.

## aws mode: IRSA roles
Each aws-edge ServiceAccount is annotated `${irsa_role_prefix}-<suffix>`; the site's IaC
creates the role with a trust for exactly that `namespace:serviceaccount`. Where a bucket
defaults to SSE-KMS, each role also needs `kms:Decrypt` (+ `kms:GenerateDataKey` to write).

| role suffix | namespace | ServiceAccount | S3 access |
| --- | --- | --- | --- |
| `-hive-metastore` | `${hive_namespace}` | `hive-metastore` | lake read/write |
| `-trino` | `${scout_analytics_namespace}` | `trino` | lake read |
| `-trino-rw` | `${scout_extractor_namespace}` | `trino-rw` | lake read/write |
| `-hl7log-extractor` | `${scout_extractor_namespace}` | `hl7log-extractor` | HL7 source read; lake + scratch read/write |
| `-hl7-transformer` | `${scout_extractor_namespace}` | `hl7-transformer` | lake read/write; scratch read |
| `-opa-bundle-writer` | `${keycloak_namespace}` | `keycloak-opa-bundle-writer` | OPA bundle bucket write |
| `-opa-bundle-reader` | `${scout_analytics_namespace}` | `opa-trino` | OPA bundle bucket read |

`hive-metastore-readonly` carries the writer annotation but does no S3 I/O (no storage
authorization listener, SELECT-only DB role).

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
  upgrade, or force it with `flux reconcile hr keycloak-config-cli -n <ns> --force`.
- Every enabled component's key must be present. config-cli fails the whole realm
  import on an unresolved `$(env:...)` (`undefined-is-error` defaults to true), so a
  missing key blocks every realm change. Provision `keycloak-client-secrets`
  fail-closed (an ExternalSecret that errors if a source key is absent), and only enable
  an IdP or the XNAT client once its key exists.
