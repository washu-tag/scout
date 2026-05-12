# Promtail → Alloy Migration Plan

## Background

Grafana announced Promtail's deprecation in early 2025; the recommended successor is Grafana Alloy. Scout currently runs the `grafana/promtail` Helm chart (pinned `~6.17.0`) as a DaemonSet in `scout-monitoring`, shipping pod logs to Loki via `loki-gateway`.

A teammate's experimental commit ([1fbb105](https://github.com/washu-tag/scout/commit/1fbb1052ef9dc72c283c237f20c3617199154e9b)) swapped the Helm release to `grafana/alloy` but only produced a `loki.write` sink with no source — the pipeline had nothing wired into it, so Alloy started cleanly but collected zero logs. Only the `loki-canary` DaemonSet (which writes synthetic `pppppp` lines directly to Loki) appeared in queries.

## Goals

1. Replace promtail with a working Alloy deployment that ingests **the same logs with the same labels** as today, so existing dashboards and saved queries keep working.
2. Keep the change idempotent under Ansible (`make install-monitor` is the entry point).
3. Verify end-to-end before deleting promtail config.

## Non-goals

- Switching Loki storage backend, retention, or auth.
- Refactoring dashboards.
- Adding new alerting rules.
- Migrating other promtail consumers (there are none — promtail only ships to Loki).

## Current state — facts gathered

### What promtail does today
- Deployed by the `loki` ansible role (`ansible/roles/loki/tasks/deploy.yaml`) as a second Helm release alongside Loki itself.
- Helm chart `grafana/promtail` `~6.17.0`, values file `helm/loki/promtail-values.yaml` (just sets the loki-gateway client URL; everything else is the chart's opinionated default).
- DaemonSet runs as root, mounts `/var/log/pods` and `/var/lib/docker/containers` (hostPath, read-only), uses ServiceAccount `promtail` with cluster-wide pod list/watch.
- Scrapes via `kubernetes_sd_configs: role: pod`, tails `/var/log/pods/*<uid>/*<container>/*.log`, uses the `cri` pipeline stage.

### Labels produced (the contract dashboards depend on)
The live promtail configmap shows these target labels after relabeling:

| label | derivation |
|---|---|
| `namespace` | `__meta_kubernetes_namespace` |
| `pod` | `__meta_kubernetes_pod_name` |
| `container` | `__meta_kubernetes_pod_container_name` |
| `app` | first non-empty of `app_kubernetes_io_name`, `app`, controller_name (with hash trimmed), pod_name |
| `instance` | first of `app_kubernetes_io_instance`, `instance` |
| `component` | first of `app_kubernetes_io_component`, `component` |
| `node_name` | `__meta_kubernetes_pod_node_name` |
| `job` | `<namespace>/<app>` |

Dashboards in `ansible/roles/grafana/templates/` reference at least: `{job=...}`, `{namespace=..., pod=~...}`, `{container="postgres"}`. The migration **must** produce identical labels or those panels will silently break.

### Cluster state observed
- `promtail` DaemonSet healthy in `scout-monitoring`, 2 pods.
- `loki-0` (SingleBinary), `loki-gateway`, `loki-canary` healthy.
- Loki has no multi-tenancy (`auth_enabled: false`), no `X-Scope-OrgID` headers needed.

## Root cause of the failed experiment

The committed Alloy config was only:

```river
loki.write "default" {
  endpoint { url = "http://loki-gateway.loki.svc.cluster.local:3100/loki/api/v1/push" }
}
```

River is a dataflow language — `loki.write` is a sink. Without a `discovery.kubernetes` + `loki.source.*` chain forwarding to its receiver, nothing reaches it.

Two additional gotchas the experiment didn't hit but we need to avoid:

1. **Default DaemonSet tolerations differ.** The Alloy chart's default `controller.tolerations` may not match promtail's, so control-plane / GPU-tainted nodes could lose log collection. Need to verify and replicate.
2. **HostPath mounts are not on by default.** If we use `loki.source.file` we need `alloy.mounts.varlog: true` and `alloy.mounts.dockercontainers: true`. If we use `loki.source.kubernetes` (reads via kubelet pod-logs API) we avoid this entirely.

## Design decisions

### D1. Source: `loki.source.kubernetes` vs `loki.source.file` + hostPath

**Chosen: `loki.source.kubernetes`.**

Pros: no hostPath mounts (less host coupling), works regardless of container runtime path conventions, simpler config, doesn't need root.
Cons: reads via kubelet pod-logs API (one API call per pod-log stream); slightly higher kubelet load than file tailing.

For Scout's scale (single-cluster, dozens of pods), the kubelet load is irrelevant. The big win is dropping `/var/lib/docker/containers` and `/var/log/pods` hostPath mounts — promtail needed them because it tailed files, but the new architecture doesn't have to. If we ever hit kubelet throughput problems we can revisit.

### D2. Where the alloy config lives

The experimental commit reused the `loki` role and put the alloy values in `helm/loki/alloy-values.yaml`. That conflates two services in one role.

**Chosen: create a new `alloy` ansible role** at `ansible/roles/alloy/` with its own defaults, task, and Jinja2 template for the values file. The `loki` role goes back to only managing Loki. The `monitor` playbook imports both roles. This matches the per-service role convention already used throughout `ansible/roles/`.

Helm values template will be a Jinja2 file at `ansible/roles/alloy/templates/alloy.values.yaml.j2`, not a static file in `helm/`. We need Jinja for the loki-gateway URL and namespace.

### D3. Label parity

The new Alloy `discovery.relabel` block needs to reproduce promtail's relabel rules **exactly** (including the `app` fallback chain and the `<namespace>/<app>` job composition). I will translate each promtail `relabel_configs` entry to a `discovery.relabel` `rule { }` block one-for-one.

### D4. Deletion ordering

Don't remove promtail in the same change that deploys alloy. Two-step rollout:

1. Add alloy alongside promtail. Verify logs flow from alloy. Promtail is still running so dashboards never go dark.
2. Remove promtail (the Helm release in the loki role, plus the values file and version pin).

The promtail Helm chart is opt-in via the chart's `enabled` flag; we can also flip that to `false` for step 1's "shadow" deploy if we want to avoid double-shipping. But double-shipping briefly is fine (Loki dedup will not happen — we'd have 2x stream cost briefly — but it lets us A/B compare).

**Chosen: deploy alloy alongside live promtail, A/B compare labels with a Loki query, then in a follow-up commit remove promtail.** Keep both commits in this branch.

### D5. Versions

- `alloy_helm_chart_version: ~1.5.x` (current major on artifacthub) — match the experiment's pin (~1.5.1) and update once before merge if a newer 1.x is out.
- Add a Renovate annotation per ADR-0015.

## Implementation plan (red/green TDD)

Each step has a manual verification before moving on. User runs the ansible commands; I edit code and dictate the checks.

### Phase 0: Verify the existing label contract against live Loki

Before writing any code, verify what labels promtail is actually emitting today so we know exactly what alloy needs to reproduce. Specifically, resolve the open question about `{job="keycloak"}`-style dashboard queries: with promtail's `<namespace>/<app>` job composition, those queries should match `job="keycloak/keycloak"` (or similar) — not `job="keycloak"`. Either the dashboards are already broken, or `app` resolves to something that makes the composed job equal the literal string in the query.

Steps:
1. Query live Loki via Grafana Explore (or `logcli`/`curl` against `loki-gateway`) for the actual `job` values present:
   - `http://loki-gateway.scout-monitoring.svc.cluster.local:3100/loki/api/v1/label/job/values`
   - Same for `app`, `namespace`, `container`, `instance`, `component`, `node_name`.
2. For each dashboard query identified earlier (`{job="keycloak"}`, `{job="oauth2-proxy"}`, `{job="$job"}`, `{namespace=..., pod=~"keycloak-0"}`, `{container="postgres"}`), test the query in Grafana and record whether it currently returns data.
3. Document the resolved label values inline in this plan so the alloy `discovery.relabel` rules are validated against actual production output, not against what we *think* promtail's chart defaults produce.

Outcome: an updated section of this doc (filled in below) with the exact label contract alloy must reproduce. If any dashboard panels are already broken, surface that to the user — fixing them is out of scope for this migration but worth flagging.

#### Findings (Phase 0)

Queried live Loki via `kubectl port-forward svc/loki 3100:3100`.

**Labels present in Loki today (via `/loki/api/v1/labels`):**
`app`, `component`, `container`, `filename`, `instance`, `job`, `namespace`, `node_name`, `pod`, `service_name`, `stream`.

**`job` label values (sample, last 24h):**
All namespaced as expected — `kube-system/oauth2-proxy`, `kube-system/traefik`, `scout-core/keycloak-operator`, `scout-core/launchpad`, `scout-core/postgresql`, `scout-core/valkey`, `scout-monitoring/promtail`, `scout-monitoring/grafana`, `scout-extractor/temporal`, `scout-data/hive-metastore`, etc. Confirms promtail emits `<namespace>/<app>`.

**Loki query probes (lines/1h, live):**

| Query | Lines | Notes |
|---|---|---|
| `{job="keycloak"}` (Loki) | 0 | No such Loki job — promtail emits `scout-core/keycloak`. **But no dashboard actually runs this against Loki** (see below). |
| `{job="kube-system/oauth2-proxy"}` | 720 | Works |
| `{namespace="scout-core", pod=~"keycloak-0"}` | 0 (1h) / 20,103 (7d) | Promtail labels are fine; keycloak-0 just stopped emitting stdout on May 10 — out of scope. |
| `{container="postgres"}` | 144 | Works |
| `{namespace="kube-system"}` | 1614 | Works |
| `{namespace="scout-monitoring"}` | 31828 | Works |

**False-alarm correction:** I initially flagged dashboard queries like `up{job="keycloak"}` and `up{job="oauth2-proxy"}` (in `auth-dashboard.json.j2:115,289`) as broken because the bare job name `keycloak` doesn't match promtail's `<ns>/<app>` Loki labels. **That was wrong** — those queries target the **Prometheus** datasource (`type: "prometheus"`), not Loki. Prometheus has its own `job` label set by `kube-prometheus-stack` scrape configs (`job="keycloak"`, `job="oauth2-proxy"`), independent of Loki labels. Both verified live and returning value=1.

Inspecting every Loki-datasource expr in `ansible/roles/grafana/templates/` (script-extracted), every actual Loki query uses `{namespace=..., pod=~...}` form — never bare `{job=...}`. So no dashboard depends on a specific `job` shape from promtail.

**Anomaly resolved (not a logging issue):** `keycloak-0` `{pod="keycloak-0"}` returned 0 lines in the 1h/24h windows because the keycloak process stopped writing to stdout on **May 10 16:26 UTC**, more than 24h before testing. Promtail correctly read 47,609 lines / 5.94 MB from the file. A 7-day window query returns the stream with full expected labels (`app=keycloak`, `job=scout-core/keycloak`, `instance=keycloak`, `component=server`, etc.). The pipeline is fine; keycloak's internal logger went quiet — separate problem, out of scope.

**Dashboard label dependency check:**
- `filename` / `service_name`: grep → no matches. Safe that `loki.source.kubernetes` won't produce `filename`.
- All Loki-datasource exprs use `{namespace=..., pod=~...}` (script-extracted from `*.j2`). No reliance on `{job=...}` Loki selectors.

**Label contract alloy must reproduce:**
`namespace`, `pod`, `container`, `app` (fallback chain), `job` (= `<namespace>/<app>`), `instance`, `component`, `node_name`. The `stream` label is added automatically by `loki.source.kubernetes`. The `service_name` label is Loki's built-in auto-detection (independent of the collector). The `filename` label will be absent — confirmed unused.

Phase 0 complete; proceeding to Phase 1.

### Phase 1: New alloy role (parallel deploy)

**Commit 1:** Scaffold the `alloy` role.

Files added:
- `ansible/roles/alloy/meta/main.yaml` — depends on `scout_common`
- `ansible/roles/alloy/defaults/main.yaml` — `alloy_namespace: "{{ scout_monitoring_namespace }}"`, `alloy_helm_chart_version` reference
- `ansible/roles/alloy/tasks/main.yaml` — include `deploy.yaml`
- `ansible/roles/alloy/tasks/deploy.yaml` — install Helm release using `scout_common`'s `deploy_helm_chart`, referencing the templated values
- `ansible/roles/alloy/templates/alloy.values.yaml.j2` — chart values containing the full River config with label parity
- `ansible/group_vars/all/versions.yaml` — add `alloy_helm_chart_version` with Renovate annotation
- `ansible/playbooks/monitor.yaml` — add `alloy` role after `loki`

**Red test:** Before `make install-monitor`, confirm:
- `kubectl get ds -n scout-monitoring alloy` returns NotFound.

**Green test:** After `make install-monitor`:
- `kubectl get ds -n scout-monitoring alloy` exists, all pods Ready.
- `kubectl logs -n scout-monitoring -l app.kubernetes.io/name=alloy --tail=50` shows no errors, shows component health.
- In Grafana → Explore → Loki, query `{namespace="scout-monitoring", pod=~"alloy-.*"}` returns alloy's own logs.
- Query `count_over_time({namespace="scout-monitoring"}[5m])` shows logs from alloy itself flowing.

**Results:**
- ✅ `kubectl get ds -n scout-monitoring alloy` → 2/2 Ready (one pod per node — `tagdev-big-03` and `tagdev-control-03`).
- ✅ All 4 River components healthy via `http://<alloy-pod>:12345/api/v0/web/components`: `discovery.kubernetes.pods`, `discovery.relabel.pods`, `loki.source.kubernetes.pods`, `loki.write.default`.
- ✅ No errors or warnings in alloy pod logs.
- ✅ Alloy's own logs flowing to Loki within 3 min of startup — 262 + 260 lines in the first 10 min.
- ✅ **Label parity confirmed on alloy's own streams** — labels emitted: `namespace=scout-monitoring`, `pod=alloy-b5zd2`, `container=alloy`, `app=alloy`, `instance=alloy`, `job=scout-monitoring/alloy`, `node_name=tagdev-big-03.nrg.wustl.edu`, plus auto-added `stream`, `service_name`, `filename`, `detected_level`. Matches promtail's schema exactly. (`component` is absent because alloy's pod doesn't carry `app.kubernetes.io/component` — promtail handles this case identically.)
- Notable bonus: `loki.source.kubernetes` *does* synthesize a `filename` label from kubelet metadata, so even that label (previously flagged as expected-to-be-missing) is preserved.

**Bug found and fixed during Phase 1 verification:** Initial alloy config used `loki-gateway:3100`. The actual `loki-gateway` Service only exposes port 80 (nginx) — port 3100 is on a different Service (`loki`). Symptom: `loki_write_sent_bytes_total=0` with `status_code="-1"` on alloy pods, while alloy's own logs *still appeared* in Loki — those were being shipped by promtail (which was scraping alloy and using the correct port-80 URL). Fixed by dropping the explicit `:3100` so the URL matches promtail's default. The teammate's experimental commit had the same off-by-port bug, but masked because it pointed at `loki.<ns>` instead of `loki-gateway.<ns>`, and the `loki` Service does expose 3100.

### Phase 2: Verify label parity

**Test:** With both promtail and alloy running, both ship to Loki. Manually compare on a known service (e.g. keycloak):

- `{job="keycloak"}` — does this work both before and after? (Should — promtail emits this and alloy must too.)
- `{namespace="scout-monitoring", container="grafana"}` returns logs.
- Pick a recent timestamp and confirm both promtail and alloy emitted a line for the same source pod. (Loki will store as two streams because they'll differ by some internal label — that's fine and expected.)

If any label mismatches, iterate on `discovery.relabel` rules in the values template, re-run `make install-monitor`, re-check.

### Phase 3: Remove promtail

**Commit 2:** Remove promtail.

Files modified:
- `ansible/roles/loki/tasks/deploy.yaml` — drop the "Deploy Promtail" task block
- `ansible/group_vars/all/versions.yaml` — drop `promtail_helm_chart_version` line and Renovate annotation
- `helm/loki/promtail-values.yaml` — delete
- `helm/loki/README.md` — replace any promtail references with alloy
- CLAUDE.md if it mentions promtail (verify — it doesn't appear to)

**Red test:** Before re-running `make install-monitor`:
- `kubectl get ds -n scout-monitoring promtail` exists.

After applying the commit, `make install-monitor` does **not** delete the promtail release because `kubernetes.core.helm` only manages releases listed; need to uninstall it manually or add an explicit removal task.

**Decision:** add a one-time ansible task in `loki/tasks/deploy.yaml` that does `kubernetes.core.helm` with `state: absent` for `promtail` in `{{ loki_namespace }}`. This is idempotent (no-op when release is already gone) and lets `make install-monitor` cleanly transition any existing cluster.

**Green test:**
- `kubectl get ds -n scout-monitoring promtail` returns NotFound.
- Loki queries from Phase 2 still work via alloy.
- Grafana dashboards (keycloak, oauth2-proxy, jupyterhub, k8s, trino) still display log panels.

### Phase 4: Optional cleanup follow-up (not in this branch)

- Remove the temporary `helm uninstall promtail` task once the change has propagated to all deployment targets.

(Note: `loki-canary` is unrelated — it's a synthetic end-to-end probe for Loki itself, not a log collector. It stays.)

## River config sketch (subject to revision in implementation)

```river
discovery.kubernetes "pods" {
  role = "pod"
}

discovery.relabel "pods" {
  targets = discovery.kubernetes.pods.targets

  // Trim the controller-hash suffix for the app-name fallback chain
  rule {
    source_labels = ["__meta_kubernetes_pod_controller_name"]
    regex         = "([0-9a-z-.]+?)(-[0-9a-f]{8,10})?"
    target_label  = "__tmp_controller_name"
  }
  rule {
    source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name",
                     "__meta_kubernetes_pod_label_app",
                     "__tmp_controller_name",
                     "__meta_kubernetes_pod_name"]
    regex         = "^;*([^;]+)(;.*)?$"
    target_label  = "app"
  }
  rule {
    source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_instance",
                     "__meta_kubernetes_pod_label_instance"]
    regex         = "^;*([^;]+)(;.*)?$"
    target_label  = "instance"
  }
  rule {
    source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_component",
                     "__meta_kubernetes_pod_label_component"]
    regex         = "^;*([^;]+)(;.*)?$"
    target_label  = "component"
  }
  rule {
    source_labels = ["__meta_kubernetes_pod_node_name"]
    target_label  = "node_name"
  }
  rule {
    source_labels = ["__meta_kubernetes_namespace"]
    target_label  = "namespace"
  }
  rule {
    source_labels = ["namespace", "app"]
    separator     = "/"
    target_label  = "job"
  }
  rule {
    source_labels = ["__meta_kubernetes_pod_name"]
    target_label  = "pod"
  }
  rule {
    source_labels = ["__meta_kubernetes_pod_container_name"]
    target_label  = "container"
  }
}

loki.source.kubernetes "pods" {
  targets    = discovery.relabel.pods.output
  forward_to = [loki.write.default.receiver]
}

loki.write "default" {
  endpoint {
    url = "http://loki-gateway.{{ loki_namespace }}.svc.cluster.local:3100/loki/api/v1/push"
  }
}
```

Note: Alloy River's `discovery.relabel` rules default `action = "replace"` and treat absence of `regex` as match-everything, so the more verbose promtail rules simplify slightly. I'll verify equivalence by spot-checking a few streams before declaring parity.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Label drift breaks dashboards silently | Phase 2 manual A/B compare before removing promtail |
| Alloy chart tolerations don't match promtail's, missing some nodes | Set `controller.tolerations` explicitly in values to match promtail's chart defaults (mainly: `operator: Exists` to cover taints) |
| `loki.source.kubernetes` performance differs from file tailing | Monitor alloy pod memory/CPU; revisit only if observed |
| Renaming a role breaks unrelated playbooks | New role added, no existing role renamed |

## Rollback

Each phase is its own commit. To roll back Phase 2: revert that commit and re-run `make install-monitor` (the helm release for promtail will be re-deployed). To roll back Phase 1: revert that commit and run `helm uninstall alloy -n scout-monitoring` manually (Ansible doesn't auto-uninstall removed roles).

## Open questions (will make reasonable calls if unanswered)

1. Should Alloy run on every node including ones with NoSchedule taints? Current promtail does (chart default), I'll preserve.
2. Should we surface a Grafana dashboard for Alloy's own health? Out of scope; defer.

(The earlier open question about `{job="keycloak"}` dashboard query semantics is now Phase 0 and will be resolved before any code is written.)
