# Phase 3 follow-up: the auth-overlay workstream (Keycloak realm decomposition + Temporal authz/web)

Status: NOT started. Deferred out of the Phase 3 `deploy/` base sweep on purpose.
This is the "item #9" from the Phase 3 completion plan. It is a single cohesive
workstream, not a grab-bag, and it is gated on a spike that needs a live cluster.

## Why it is split out

The Phase 3 `deploy/` base sweep (compute sizing, postgres, literal-to-var swaps,
oauth2-proxy templates, the extractor ingest-DB creator, per-namespace foundation
bases, CI hardening, MinIO de-dup) is behavior-preserving scaffold work. This
follow-up is different: it is new auth functionality that, done wrong, breaks all
login. It should be built and reviewed on its own, spike-first.

## What is in scope

1. Keycloak realm decomposition (staged): split the monolithic realm into a base
   realm plus feature-gated fragments for the `xnat` and `chat` clients, so a site
   that does not run those features does not carry their OIDC clients.
2. Temporal frontend JWT authorization (the `authorization.authorizer: default`
   block the Ansible role renders in `values.yaml.j2`).
3. Temporal web UI ingress + OIDC (also rendered by the Ansible role, omitted from
   the `deploy/base/temporal/server` base today).
4. The optional Temporal `ScheduledReportIngest` cron. This one is independent of
   the auth work (see below).

Items 2 and 3 depend on item 1: `deploy/base/temporal/server/resources.yaml` states
plainly that the frontend authz and web-UI OIDC are "Keycloak-realm decomposition +
site-edge auth-overlay work, a separate workstream," which is why Keycloak is
deliberately not a `dependsOn` of the temporal-server layer. They cannot land ahead
of the realm decision.

## Ground truth (what makes this tractable and what makes it risky)

Delivery mechanism, confirmed:
- The realm is CI-seeded into the fixed-name `keycloak-config` Secret (key
  `scout-realm.json`, base64 of the rendered `scout-realm.json.j2`). It is never in
  git. `deploy/base/keycloak/instance/resources.yaml` mounts it via the
  keycloak-config-cli chart's `existingConfigSecret`.
- keycloak-config-cli imports every file under `IMPORT_PATH=/config/`. A Secret with
  multiple keys therefore mounts as multiple files and config-cli imports all of
  them. So the multi-file mechanism already exists; a split is a Secret-shape change
  plus CI seeding the base key always and the feature keys conditionally.
- The Ansible realm template already feature-gates xnat via
  `{% if enable_xnat | default(false) | bool %}`, so today's monolith conditionally
  includes the xnat client, its roles, and its groups. The deploy-base decomp
  replicates that gating through config-cli multi-file instead of Jinja.

The gating risk, unresolved:
- The keycloak-config-cli HelmRelease sets no `import.managed.*` override, so
  config-cli uses its defaults. With multiple files targeting the SAME realm and a
  `managed: full` resource type, config-cli can delete resources that are not in the
  file it is currently processing. Concretely: a base-realm file imported after an
  xnat-realm file, under `managed.client: full`, can DELETE the xnat OIDC client. On
  the next reconcile that is a broken realm and nobody can log in.
- Whether config-cli merges multi-file-same-realm before applying managed-sync, or
  applies per-file, is version-specific and unproven for the pinned image
  (`adorsys/keycloak-config-cli:6.5.1-26.5.5`). This is the "#1 risk / multi-file
  managed-sync unproven" the plan called out.

## The spike (do this first, on a live cluster)

Prove the multi-file managed-sync behavior before writing any decomp:
1. Stand up a throwaway Keycloak (the same operator image) + config-cli 6.5.1-26.5.5.
2. Seed a base-realm file (no xnat client) and an xnat-realm file (xnat client only)
   as two keys in one Secret, mounted at /config/.
3. Run config-cli once. Confirm the realm has BOTH the base clients and the xnat
   client after the run (i.e. the base file did not delete the xnat client).
4. Re-run config-cli with the xnat file REMOVED (simulating a site that disables the
   feature) and confirm the xnat client is removed but the base realm is intact.
5. Repeat with explicit `import.managed.client` / `role` / `group` settings until a
   configuration is found where (3) and (4) both hold. That configuration is the
   contract the decomp must ship.

If no safe managed-sync configuration exists for multi-file-same-realm, the fallback
is a single realm file with config-cli variable substitution
(`import.var-substitution`) gating the feature clients, which keeps one file and
sidesteps cross-file deletes at the cost of not being a true file split.

## Then implement

Only after the spike:
- Shape the `keycloak-config` Secret as base + per-feature keys; teach CI to seed the
  base key always and the feature keys per site (mirroring the Ansible `enable_xnat`
  gate).
- Set the proven `import.managed.*` values on the keycloak-config-cli HelmRelease.
- Add the Temporal frontend JWT authz + web-UI ingress/OIDC to
  `deploy/base/temporal/server` and make Keycloak a `dependsOn` of that layer once
  the realm provides the temporal client.

## The one piece that does not need the spike

The optional Temporal `ScheduledReportIngest` cron
(`ansible/roles/temporal/tasks/deploy.yaml`, gated on `scheduled_ingest_cron` /
`scheduled_ingest_hour`) is a plain `temporal schedule create/update` CLI Job against
the internal frontend. It has no auth entanglement and can be added to
`deploy/base/temporal/bootstrap` (alongside the retention setup) independently, if a
site wants scheduled ingest.
