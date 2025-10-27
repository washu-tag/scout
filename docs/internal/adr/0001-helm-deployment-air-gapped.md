# ADR 0001: Helm Deployment for Air-Gapped Environments

**Status**: Accepted
**Date**: 2025-10-14
**Decision Owner**: TAG Team

## Context

Scout deployments support air-gapped Kubernetes clusters where worker nodes cannot access the internet. Harbor on a staging node proxies container images. This ADR addresses Helm chart deployment.

### Network Topology (Permeable Air-Gap)

```
                         Internet
                            │
                            │
                ┌───────────▼────────────┐
                │  Staging Node          │
                │  - Harbor Registry     │
                │  - Internet Access     │
                │  - Has Kubeconfig      │
                └─────┬────────────┬─────┘
                      ▲            │
          Port 443    │            │ Port 6443
        (Harbor Pull) │            │ (k8s API)
                      │            ▼
                ┌─────┴────────────┴─────┐
                │  Air-Gapped Cluster    │
                │  - No Internet         │
                │  - API Accessible      │
                │  - Harbor Mirrors      │
                └────────────────────────┘
```

**Key characteristics:**
- Worker nodes: No internet (true air-gap)
- k8s API: Accessible from staging node (permeable)
- Harbor: Accessible from cluster nodes

Scout uses 18 Helm charts: 12 public (cert-manager, Temporal, JupyterHub, Superset, Trino, MinIO, monitoring stack, Harbor) and 6 local (explorer, extractors, hive-metastore, PACS).

## Decision

**Use remote Helm deployment via kubeconfig for air-gapped environments.**

Ansible control node deploys charts using `kubernetes.core.helm` module with remote kubeconfig. Helm communicates with cluster's k8s API (port 6443) and queries actual API versions. Container images pull through Harbor mirrors.

**Mode selection:**
- `air_gapped: false` → Helm runs on cluster nodes
- `air_gapped: true` → Helm runs on localhost with remote kubeconfig

## Alternatives Considered

### Summary

| Alternative | Setup | Per-Chart | Ops | Dev Burden | Total |
|-------------|-------|-----------|-----|------------|-------|
| **1. OCI Registry** | High | High | High | Medium | **Very High** |
| **2. Hybrid (OCI+Rendering)** | High | High | Medium | High | **High** |
| **3. Local Rendering** | Medium | **Very High** | Low | **Very High** | **Very High** |
| **4. Remote Helm (Selected)** | **Very Low** | **Very Low** | **Very Low** | **Very Low** | **Very Low** |

### Alternative 1: OCI Registry Cache

Cache all charts in Harbor's OCI registry, deploy from there.

**Pros:** Full Helm features, true air-gap
**Cons:** 12 public charts require manual `helm pull` + `helm push` sync on every version update. High operational overhead.

### Alternative 2: Hybrid (OCI for Local, Rendering for Public)

Local charts via GHCR OCI, public charts via `helm template` + `kubectl apply`.

**Pros:** Avoids caching public charts
**Cons:** Two deployment methods to maintain, test, and document. Inconsistent patterns.

### Alternative 3: Local Rendering

Render all charts with `helm template` on localhost, apply with `kubectl`.

**Pros:** No chart caching, works everywhere
**Cons:** Requires manual API version discovery per chart (e.g., `helm_chart_api_versions: ["cert-manager.io/v1"]`). No Helm rollback/history/hooks. Cryptic errors. High developer burden.

**Example failure:**
```
Error: template: cass-operator/templates/webhooks.yaml:5:10:
       <.Capabilities.APIVersions.Has "cert-manager.io/v1">:
       wrong type for value; expected bool; got string
```

Developers must investigate chart templates to discover required API versions.

### Alternative 4: Remote Helm Deployment (Selected)

Deploy from localhost using remote kubeconfig.

**Pros:**
- Full Helm features (rollback, history, hooks, atomic installs)
- No API version parameters needed (Helm queries cluster directly)
- Standard Helm workflow (no specialized knowledge)
- Leverages existing kubeconfig infrastructure

**Cons:**
- Requires k8s API port 6443 accessible (already needed for kubectl)
- Not 100% air-gapped during deployment (acceptable for Scout's permeable model)

## Rationale

Scout's staging node already has k8s API access. Using remote Helm eliminates:
- Manual API version discovery for every chart
- Per-chart investigation and debugging
- Custom rendering role maintenance
- Documentation of Helm templating workarounds

Developers use standard Helm workflow. No specialized knowledge required.

### Trade-offs

| Trade-off | Assessment |
|-----------|------------|
| k8s API dependency | Acceptable (API required for kubectl anyway) |
| Not 100% air-gapped | Acceptable (permeable model by design) |
| Network latency | Negligible (seconds in multi-minute deployments) |

## Implementation

**Configuration variable:** `air_gapped: true/false`

**Delegation logic:**
- Air-gapped: `delegate_to: localhost` with `local_kubeconfig_yaml`
- Non-air-gapped: Run on cluster nodes (or localhost for local charts)

**No changes required to chart definitions.** Standard Helm parameters work for all charts.

## References

- [Helm Documentation](https://helm.sh/docs/)
- [Ansible kubernetes.core.helm module](https://docs.ansible.com/ansible/latest/collections/kubernetes/core/helm_module.html)
- `staging-node-architecture.md` - Harbor setup
