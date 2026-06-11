# ADR 0027: XNAT Plugin Delivery

**Date**: 2026-06-10
**Status**: Proposed
**Decision Owner**: TAG Team

> 🚧 **DRAFT — NOT FOR MERGE AS-IS.** This ADR documents the plugin-delivery
> mechanism for the in-development XNAT feature (`enable_xnat`, see ADR 0026).
> The mechanism is still evolving, and much of it is intended to be upstreamed
> into the chart. Passages tagged **[UNSTABLE]** must be reviewed and rewritten
> to match what actually ships before merge. Because an ADR becomes immutable
> once merged with its feature, write this to describe the mechanism Scout
> *actually ships at merge time* — not a promise that the ADR will later be
> revised. If the mechanism is upstreamed afterward, that is a future ADR, not
> an edit to this one.

## Context

XNAT's functionality is extended by plugins — JARs dropped into the XNAT home
`plugins/` directory and loaded at startup. Scout's deployment depends on at
least one plugin for core functionality: the `xnat-openid-auth-plugin`, which
provides the Keycloak SSO integration described in ADR 0026. Operators will also
want to add their own plugins (viewers, pipelines, site-specific extensions).

Delivering plugins into a Kubernetes deployment is not trivial:

- Plugin JARs come from heterogeneous sources — small dev builds, public
  download URLs, images with JARs baked in, and Maven/Gradle artifact
  coordinates — and an air-gapped deployment cannot reach the public internet.
- XNAT plugins ship with their own Logback configuration that writes to rolling
  **files**, which is wrong for Kubernetes, where logs are expected on stdout.
  XnatWorks' own approach has been to build a bespoke container image per
  plugin to handle this; Scout wants to avoid maintaining per-plugin images.

This ADR records how Scout gets plugin JARs into XNAT and how it makes their
logs Kubernetes-friendly.

> ⚠️ **[UNSTABLE — scope likely to shrink via upstreaming]**
> Most of the installer mechanics below (the multi-source init container and the
> Logback rewrite) *should eventually live in the upstream chart* rather than in
> Scout. The set of supported delivery patterns may also consolidate as the
> chart's native capabilities improve. Confirm the actual shipped scope before
> merge and trim this ADR to match.

## Decision

Install plugins through a **Scout-built `xnat-plugin-installer` init container**
that acquires each plugin JAR from one of several source types, **rewrites its
Logback configuration to log to stdout**, and copies it into a shared volume
that XNAT mounts at startup. The required `xnat-openid-auth-plugin` is a role
default that operators' plugin lists are **added to**, not replaced.

### 1. Multiple plugin delivery patterns

A plugin entry declares its source, and the role supports several patterns so
operators can use whatever form an artifact is available in:

- **Secret-mounted JAR** — a JAR carried in a Kubernetes Secret and copied into
  place. Useful for small dev/in-house builds; air-gap friendly.
- **URL download** — the init container downloads the JAR from an HTTPS URL.
  Simplest when the artifact is on a reachable host; needs egress.
- **Image-baked (`plugins:`)** — the chart's native mechanism: JARs already
  present in a container image. Assumes that image already logs to stdout, so it
  bypasses the installer's rewrite (see §2).
- **Maven/Gradle coordinates** — the init container resolves the artifact by
  Maven coordinate. Air-gap friendly and the cleanest for GitOps, since the
  plugin identity is a declarative coordinate rather than a binary or a URL. The
  coordinate is a Maven `-Dartifact` value,
  `groupId:artifactId:version[:packaging[:classifier]]`; a classified artifact
  like the openid `-xpl.jar` is packaging `jar` + classifier `xpl`
  (`...:jar:xpl`). Where the artifact is resolved from is config, not part of the
  coordinate — see §4.

Patterns that flow through the installer (Secret, URL, coordinates) get the
Logback rewrite; the image-baked pattern does not.

> ⚠️ **[UNSTABLE — pattern set may consolidate]**
> The exact set of patterns and their precedence is still settling. Re-verify
> against the shipped role before merge and remove any pattern that does not
> actually ship.

### 2. Logback rewrite to stdout

The installer rewrites each plugin's bundled Logback configuration from a
rolling **file** appender to a **console** appender so plugin logs land on
stdout and are captured by the normal Kubernetes/Loki logging path. The rewrite
preserves the original encoder pattern (and disambiguates appender names so logs
from multiple appenders remain distinguishable). This replaces XnatWorks'
practice of building a separate container image per plugin purely to fix
logging, so Scout can consume stock plugin artifacts without per-plugin image
maintenance.

### 3. Required openid plugin is a default; operator plugins are additive

The role defines the `xnat-openid-auth-plugin` as a built-in default plugin, and
the effective plugin set is `default + operator-supplied` (i.e.
`xnat_plugins_all = xnat_plugins_default + xnat_plugins`). Operators declare only
the plugins they are *adding*; the required SSO plugin cannot be accidentally
dropped or duplicated. There is one source of truth for "the plugin XNAT needs
to authenticate at all."

### 4. Coordinate resolution: generic image, role-supplied repo config + CA

The installer image is a **generic Maven runner with no repository or
air-gapped knowledge baked in.** It resolves `-Dartifact=<coordinate>` and uses
a Maven `settings.xml` (mounted at `/mnt/maven/settings.xml`) and a CA
certificate **only if the deployment mounts them**; with neither, Maven resolves
from its built-in Central. All "which repo / air-gapped" policy therefore lives
in the Ansible role and inventory, never in the published image — the image
needs no rebuild to change repositories or to move between connected and
air-gapped clusters.

The role decides whether to mount a `settings.xml`, and what it contains, from
**two independent triggers**:

- **A plugin's `repo_url`** — optional, per plugin. Omit it for artifacts on
  Maven Central; set it only when the artifact lives elsewhere. The openid plugin
  sets it because it is published *only* to the NrgXnat Artifactory
  (`libs-release`), never to Central.
- **Air-gapped mode** — no egress to Central, so resolution must go through the
  in-cluster proxy.

The generated `settings.xml` differs accordingly:

- **Air-gapped:** *mirror* all resolution (`<mirrorOf>*</mirrorOf>`, including
  Maven's own download of the dependency plugin) through the Nexus `scout-maven`
  group, which proxies Maven Central **and** (when `enable_xnat`) the NrgXnat
  Artifactory. Per-plugin
  `repo_url`s are subsumed here — an external URL is unreachable air-gapped, so
  its upstream must instead be a member of the Nexus group (as `xnat-maven` is for
  the NrgXnat Artifactory). This extends the package-proxy pattern from ADR 0017.
- **Not air-gapped:** keep Maven Central as the default and *add* each plugin's
  `repo_url` as an extra repository. A Central-only plugin needs no `settings.xml`
  at all.

When the air-gapped Nexus serves HTTPS with the staging node's self-signed cert,
the role also mounts that CA (per ADR 0016) and the installer imports it into the
JVM truststore so Maven's TLS validates. Like the `settings.xml`, the CA is a
mounted input the generic image consumes only when present.

### 5. Air-gapped URL plugins fail fast (restaging deferred)

On an air-gapped cluster, a URL-pattern plugin's host is unreachable from
inside the cluster, so the role asserts against `source.type=url` plugins when
`air_gapped` is true and fails the deploy with guidance to use **coordinates**
(§1/§4, which resolve through the Nexus maven proxy and need no egress),
**image**, or **file** instead.

> **Future enhancement (not implemented):** re-hosting URL-only artifacts via a
> Nexus raw repository — the jump node fetches each JAR, uploads it to a raw
> hosted repo, and the init container's URL is rewritten to the in-cluster
> Nexus address. An earlier draft of this mechanism was removed before merge:
> it had no consumer (every known plugin resolves by coordinates), and a
> correct implementation needs CA trust for the init container's fetch,
> exclusion from Nexus's purge-unused cleanup policy (a hosted raw repo is the
> only in-air-gap copy, not a re-fillable cache), and checksummed, idempotent
> uploads. Revisit if a plugin ever exists only as a URL download.

## Consequences

### Positive

- Operators can install plugins from whatever form an artifact exists in,
  including in air-gapped clusters, without Scout building a custom image per
  plugin.
- Plugin logs appear on stdout like every other Scout workload, so they flow
  into Loki/Grafana with no special handling.
- The required SSO plugin is guaranteed present (additive default), removing a
  whole class of "XNAT came up but nobody can log in" misconfigurations.
- Coordinate-based plugins are fully declarative and air-gap friendly through
  the existing Nexus proxy.

### Negative / Accepted trade-offs

- The installer and Logback rewrite are Scout-maintained machinery that
  *should* live upstream; carrying them in Scout is accepted as interim.
- Multiple delivery patterns mean more surface area to document and test than a
  single canonical path.
- URL restaging adds a jump-node step in air-gapped deployments (mitigated by
  preferring the coordinates pattern).

### Operational

- Coordinate and air-gapped URL plugins depend on Nexus (ADR 0017) and, where
  TLS is self-signed, staging certificate trust (ADR 0016).
- When the upstream chart absorbs the installer/rewrite responsibilities, the
  corresponding Scout machinery should be retired (a future change, not an edit
  to this ADR).

## Related

- **ADR 0016**: Staging Node Certificate Distribution — TLS trust for
  Nexus-hosted artifacts.
- **ADR 0017**: Air-Gapped Package Proxy — the Nexus Maven proxy that resolves
  coordinate-based plugins, and the proxy pattern this ADR extends.
- **ADR 0026**: XNAT Deployment Posture and Lifecycle — the deployment this
  plugin mechanism feeds, including the SSO plugin's role in authentication.
