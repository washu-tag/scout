# ADR 0026: XNAT Deployment Posture and Lifecycle

**Date**: 2026-06-10
**Status**: Proposed
**Decision Owner**: TAG Team

> **Note:** XNAT ships as an opt-in, single-node feature. One item remains an
> open follow-up, called out inline below: `enable_xnat: false` skips the play
> rather than actively tearing the workload down (§1).

## Context

XNAT (the imaging informatics platform — repo
<https://github.com/nrgxnat/xnat>; historically published as `xnatworks/xnat-web`
on Docker Hub, migrating to a GHCR-hosted image `ghcr.io/nrgxnat/xnat`) is being
added to Scout as an **optional** service for
storing and working with DICOM imaging studies. It is deployed via Ansible on
top of the upstream Helm chart — published as an OCI artifact
(`oci://ghcr.io/nrgxnat/charts/xnat`) and pinned by chart version —
the same way Scout deploys its other services.

Adding XNAT raises a set of deployment decisions that are not obvious from the
chart alone: how the feature is gated, how a first boot is made non-interactive
and reproducible, how it integrates with Scout's existing Keycloak/oauth2-proxy
authentication, how it is sized, and how it stores data. This ADR records those
decisions. Plugin delivery — a large enough topic on its own — is covered
separately in ADR 0027.

XNAT is a stateful Java/Hibernate application backed by PostgreSQL. Out of the
box its first run launches an interactive setup wizard and seeds a default
`admin:admin` account. Neither is acceptable for an automated, multi-tenant
platform deployment, which shapes several of the decisions below.

## Decision

Deploy XNAT as an **opt-in feature** behind a single `enable_xnat` flag,
configured non-interactively via a templated first-boot preferences file,
fronted by Scout's standard oauth2-proxy edge gate plus an application-level
OIDC client, and sized for a **single-node deployment** with dynamically
provisioned storage following existing Scout conventions.

### 1. Opt-in behind a single feature flag

`enable_xnat` (default `false`) is the single control point for the feature.
When it is `false`:

- the XNAT playbook ends before the role runs, so no namespace, workload, or
  configuration is created; and
- the Keycloak realm template omits the `xnat` client **and** the `xnat-access`
  role.

When it is `true`, both the deployment and its Keycloak wiring are created
together. There is exactly one switch; there is no half-on state.

**XNAT-related secrets are required only when the feature is enabled.**
`keycloak_xnat_client_secret` and `xnat_postgres_password` are referenced only
inside `enable_xnat`-guarded blocks (the realm template's `xnat` client block
and the XNAT role's deploy-time asserts), so an operator who never enables XNAT
never has to set them.

#### Disabling XNAT is a destructive operation (accepted)

Flipping `enable_xnat` from `true` to `false` deletes the `xnat` Keycloak client
on the next auth deploy (keycloak-config-cli reconciles the realm to match the
template). This **orphans any XNAT users provisioned through that client**. We
accept this consequence: XNAT is an optional feature, and turning it off is a
deliberate teardown action, not a routine toggle.

As shipped, `enable_xnat: false` *skips* the play — an already-deployed XNAT
keeps running until manually removed. Active teardown is a deliberate
follow-up: `enable_xnat: false` would tear down the XNAT workload (deployment /
service) while **preserving the namespace and its PVCs** so imaging data and
its backing PVs survive. (Deleting the namespace would take the PVCs — and the
orphaned PVs — with it, which is what we want to avoid.) Until that lands,
removing the workload is a manual step.

### 2. Non-interactive first boot via `prefs-init.ini`

XNAT's interactive setup wizard is bypassed by mounting a templated
`prefs-init.ini` (from the Ansible-created `xnat-prefs-init` Secret) at
`/data/xnat/home/config/prefs-init.ini`. It carries `initialized=true` plus the
site URL, admin email, the admin account password (§4), enabled auth providers,
and notification/SMTP settings, so a fresh deployment comes up fully configured
with no human at the console.

`prefs-init.ini` is a **first-boot seed**: once a preference exists in the
database, the value in `prefs-init.ini` is ignored on subsequent boots.
Day-2 changes are therefore made through the admin UI or REST API, not by
editing this file.

> **Future improvement (not v1):** XNAT also supports `prefs-override.ini` in
> the same `config/` directory, which *re-applies on every restart* regardless
> of whether the preference already exists. Promoting values we never want to
> drift (e.g. `siteUrl`, enabled providers) into an override file would make
> them enforced-on-boot. Integrating that into the role is more involved; it is
> noted here as a candidate enhancement, not part of the initial deployment.

### 3. SSO-only login surface

The site is seeded with `enabledProviders=['keycloak']`, dropping the built-in
`localdb` provider from the login page so users see only the "Sign in with
Scout" button. Provider *discovery* (the openid-auth plugin standing up the
`/openid-login` endpoint) and provider *visibility* (what the login page
offers) are independent in XNAT; this setting controls the latter.

> **Future direction:** replacing the single SSO button with an automatic
> redirect to Keycloak (skipping the XNAT login page entirely) is a candidate
> enhancement, not part of this deployment.

### 4. No default-credentialed admin account

XNAT seeds a default `admin:admin` account on first boot. Leaving that account
in place on any deployed XNAT is unacceptable, so the first-boot `prefs-init.ini`
also carries a `[system] defaultAdminPassword`, sourced from the required
`xnat_admin_password` inventory variable, which sets the `admin` account's
password during the same first-boot seed that skips the setup wizard. The
default `admin:admin` credentials therefore never survive a fresh deployment.

`xnat_admin_password` is **required when `enable_xnat` is true** — the role
asserts it is set (alongside `keycloak_xnat_client_secret` and
`xnat_postgres_password`) and fails the deploy if it is empty, so an XNAT can
never come up on the default password. It must be a strong, vault-encrypted
secret, and like every other first-boot preference it seeds a virgin instance
only: rotate it afterward through the admin UI, not by editing this file.

This replaces the earlier plan of a post-deploy job that logged in as
`admin:admin` and re-credentialed the account over the REST API — the
`defaultAdminPassword` seed does the same thing in-band at first boot, with no
extra workload and no window during which the default password is live.

### 5. Authentication: standard Scout edge gate + app-level OIDC client

XNAT follows the **standard Scout authentication pattern** (per ADR 0003 and
ADR 0012). Its Traefik ingress applies, in order:

- `oauth2-proxy-error` and `oauth2-proxy-auth` middlewares — the oauth2-proxy
  `ForwardAuth` approval gate that requires the user to be an approved
  `scout-user` before any request reaches XNAT; and
- the Scout-wide `security-headers` middleware.

Behind that gate, the `xnat-openid-auth-plugin` runs its own OIDC
authorization-code flow against a confidential `xnat` Keycloak client (client
secret, `/openid-login` redirect URI, PKCE S256, a groups mapper, and the
`xnat-access` client role mapped to the `scout-user` group). Structurally this
resembles the two-layer shape Scout uses for Grafana, Superset, and JupyterHub
— but with one important difference: **the edge gate is the only enforced
authorization layer for XNAT.** The off-the-shelf openid plugin (1.5.0) has no
role/claim-restriction property (only email-domain filtering), so it cannot
consume the `xnat-access` role, and with `forceUserCreate` any user who passes
oauth2-proxy gets an XNAT account auto-created on first login. Grafana and
Superset, by contrast, actually evaluate their client roles. `xnat-access`
is therefore provisioned but **unenforced** — kept for a future plugin-side
role check (a candidate upstream contribution); removing a user from it does
NOT revoke XNAT access today. `/openid-login` is an application callback
behind the gate, not a public path.

### 6. Single-node deployment posture

XNAT is deployed for a single-node target:

- `replicaCount: 1`,
- `redis.enabled: false` (no distributed cache), and
- `activemq.broker.enabled: false` (no distributed messaging).

These distributed subsystems are unnecessary for a single replica and are left
off to reduce moving parts. **This posture must be revisited before any HA /
multi-node deployment**, which would require multiple replicas, shared
ReadWriteMany storage, an external Redis and ActiveMQ, and re-tuned probes.

### 7. Storage: dynamically provisioned PVCs via `xnat_storage_class`

All XNAT volumes and the PostgreSQL cluster use dynamically provisioned PVCs
whose storage class comes from the `xnat_storage_class` inventory variable
(default empty). When empty, the chart omits the `storageClass` field and the
cluster's default class is used — which on Scout's K3s is `local-path`. This
matches the existing Scout convention (`minio_storage_class`,
`postgres_storage_class`, `jupyterhub_storage_class` all default to empty; see
ADR 0004).

A `local-path`-style storage class implies XNAT pods are effectively pinned to
the node where their PVs were first provisioned. This is accepted for the
single-node posture above and is part of what must be reconsidered for HA.

### 8. PostgreSQL via CloudNativePG

XNAT's database is a CloudNativePG cluster (`cnpg.cluster.enabled: true`, a
single instance, storage via `xnat_storage_class`) rather than the chart's
bundled PostgreSQL. This keeps XNAT consistent with how the rest of Scout
provisions databases, so operators reason about it the same way as other Scout
Postgres instances.

### 9. Outbound mail via Scout's shared relay

XNAT's notification mail is pointed at Scout's shared relay (MailHog in
development, the organization relay in production) via the `[notifications]`
block in `prefs-init.ini`, rather than running a per-XNAT mail server. This
reuses existing Scout mail infrastructure instead of standing up another SMTP
path. The chart's bundled Postfix subchart is left disabled
(`mail.enabled: false`), so no per-XNAT mail server runs.

## Consequences

### Positive

- One flag (`enable_xnat`) cleanly turns the entire feature — workload and auth
  wiring — on or off, and XNAT secrets are only needed when it is on.
- First boot is non-interactive and reproducible from inventory; no manual
  setup-wizard step, and no deployment ever ships with the default `admin:admin`
  credentials.
- XNAT inherits Scout's existing approval gate, security headers, storage
  convention, and database operator, so it behaves like the rest of the
  platform and reuses existing infrastructure.

### Negative / Accepted trade-offs

- Disabling XNAT is destructive to its Keycloak client and orphans provisioned
  users (accepted).
- The single-node posture (single replica, no Redis/ActiveMQ, node-pinned
  storage) is not HA and must be re-architected before a multi-node deployment.
- Day-2 preference changes bypass `prefs-init.ini` and must go through the UI or
  API until/unless `prefs-override.ini` is adopted.

### Operational

- **Admin credentials**: `xnat_admin_password` is required and seeds the `admin`
  account at first boot (§4), so the default `admin:admin` never survives a fresh
  deployment. Set it to a strong, vault-encrypted value and rotate it through the
  admin UI thereafter.
- **First-boot timing**: XNAT's first boot runs Hibernate schema DDL and can
  take several minutes; the deployment waits accordingly (probe/timeout tuning
  is an implementation detail, not a decision recorded here).
- **Mail**: the chart's bundled Postfix subchart is disabled
  (`mail.enabled: false`); XNAT routes notification mail through Scout's shared
  relay.

## Related

- **ADR 0003**: OAuth2 Proxy as Authentication Middleware — the edge approval
  gate XNAT sits behind.
- **ADR 0004**: Storage Provisioning Approach — the dynamic-provisioning /
  storage-class convention XNAT follows.
- **ADR 0011**: Deployment Portability via Layered Architecture — feature flags
  and the service-mode variable pattern.
- **ADR 0012**: Security Scan Response and Hardening — the global
  security-headers middleware applied to XNAT's ingress.
- **ADR 0025**: In-App User Administration — the Scout user-approval model that
  produces the `scout-user` membership XNAT's edge gate checks.
- **ADR 0027**: XNAT Plugin Delivery — how the openid-auth and other plugins are
  installed.
