# ADR 0026: XNAT Deployment Posture and Lifecycle

**Date**: 2026-06-10
**Status**: Proposed
**Decision Owner**: TAG Team

> 🚧 **DRAFT — NOT FOR MERGE AS-IS.** This ADR documents an in-development
> optional feature (`enable_xnat`). Passages tagged **[UNSTABLE]** describe
> behavior that is expected to change before the feature merges and **must be
> reviewed and rewritten to match what actually ships**. An ADR becomes
> immutable once merged with its feature, so do not merge this until the
> [UNSTABLE] sections reflect shipped reality — not intentions.

## Context

XNAT (the imaging informatics platform — repo
<https://github.com/nrgxnat/xnat>; the current published image is
`xnatworks/xnat-web`) is being added to Scout as an **optional** service for
storing and working with DICOM imaging studies. It is deployed via Ansible on
top of an upstream Helm chart, the same way Scout deploys its other services.

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

> ⚠️ **[UNSTABLE — teardown behavior not yet implemented]**
> Today, `enable_xnat: false` merely *skips* the play — an already-deployed XNAT
> keeps running until manually removed. The intended behavior is for
> `enable_xnat: false` to **actively tear down the XNAT workload** (deployment /
> service), while **deliberately preserving the namespace and its PVCs** so that
> imaging data and its backing PVs are not destroyed. (Deleting the namespace
> would take the PVCs — and the orphaned PVs — with it, which is exactly what we
> want to avoid.) This ADR must be updated to describe whatever teardown scope
> actually ships before merge.

### 2. Non-interactive first boot via `prefs-init.ini`

XNAT's interactive setup wizard is bypassed by mounting a templated
`prefs-init.ini` (from the Ansible-created `xnat-prefs-init` Secret) at
`/data/xnat/home/config/prefs-init.ini`. It carries `initialized=true` plus the
site URL, admin email, enabled auth providers, and notification/SMTP settings,
so a fresh deployment comes up fully configured with no human at the console.

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

> ⚠️ **[UNSTABLE — login UX expected to change]**
> The near-term direction is to replace the single SSO button with an
> **automatic redirect** to Keycloak, skipping the XNAT login page entirely.
> Update this section once the redirect behavior is decided and implemented.

### 4. No default-credentialed admin account

> 🛑 **[UNSTABLE — v1 BLOCKER, mechanism not yet implemented]**
> **This is a hard requirement and the chosen mechanism is still being built.
> Do not merge the feature — or this ADR — until both the requirement and the
> implemented mechanism are reflected here.**
>
> XNAT seeds a default `admin:admin` account, and setting `initialized=true`
> does **not** remove or change it. We must not leave a default-credentialed
> admin on any deployed XNAT.
>
> The preferences files **cannot** fix this: XNAT credentials live in
> `xdat_user.primary_password`, a different subsystem from the preference store,
> with no preference key for the password. There is no documented file- or
> env-only way to set the initial admin password at deploy time.
>
> The intended approach is a **post-deploy step** (a Kubernetes Job or init
> container) that, sourcing a strong password from a Kubernetes Secret:
> 1. waits for XNAT to respond,
> 2. authenticates as `admin:admin`,
> 3. sets a strong password on `admin` via `PUT /xapi/users/admin` — or creates
>    a dedicated admin user and disables `admin`
>    (`PUT /xapi/users/admin/enabled/false`), and
> 4. **verifies `admin:admin` no longer authenticates**, failing the rollout if
>    it does.
>
> The exact `PUT /xapi/users/{username}` payload (the `password` field and
> `authorization` block) is version-dependent and must be confirmed against the
> deployed image's in-app Swagger. Direct seeding of the password hash in
> PostgreSQL is possible but unsupported and version-brittle, and is rejected.

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
`xnat-access` client role mapped to the `scout-user` group). This is the same
two-layer shape Scout already uses for Grafana, Superset, and JupyterHub: the
edge gate enforces approval, and the app establishes its own identity via
Keycloak. `/openid-login` is an application callback behind the gate, not a
public path.

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
path.

> ⚠️ **[UNSTABLE — placeholder workaround pending upstream fix]**
> The upstream chart currently pulls in a Postfix subchart unconditionally, so
> a placeholder `postfix-password` Secret is created solely to let that pod
> start; XNAT does not route mail through it. Once the upstream chart gains a
> `mail.enabled` toggle, the subchart should be disabled and the placeholder
> Secret removed. Update this section to match whatever ships.

## Consequences

### Positive

- One flag (`enable_xnat`) cleanly turns the entire feature — workload and auth
  wiring — on or off, and XNAT secrets are only needed when it is on.
- First boot is non-interactive and reproducible from inventory; no manual
  setup-wizard step.
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

- **Admin credentials**: the post-deploy admin-password mechanism (§4) must run
  on every fresh deployment and is a release gate — see the [UNSTABLE] marker.
- **First-boot timing**: XNAT's first boot runs Hibernate schema DDL and can
  take several minutes; the deployment waits accordingly (probe/timeout tuning
  is an implementation detail, not a decision recorded here).
- **Mail**: remove the placeholder Postfix Secret once the upstream
  `mail.enabled` toggle exists.

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
