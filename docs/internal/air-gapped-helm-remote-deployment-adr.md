# Architecture Decision Record: Helm Deployment for Permeable Air-Gapped Environments

**Status**: Proposed
**Date**: 2025-10-14
**Decision Owner**: Scout Platform Team
**Supersedes**: `air-gapped-helm-architecture-decision.md` (2025-10-08)
**Related Documents**:
- `staging-node-architecture.md` (Harbor container registry and network topology)
- `air-gapped-helm-test-plan.md` (Testing approach)

## Context

Scout deployments need to support air-gapped Kubernetes clusters where worker nodes cannot directly access the internet. We've implemented Harbor on a staging node to proxy container images. The remaining challenge is Helm chart deployment.

### Current State

Scout uses 18 Helm charts across its deployment:

**Public Charts (12):**
- cert-manager (jetstack)
- cass-operator (k8ssandra)
- Temporal (temporalio)
- JupyterHub (jupyterhub)
- Superset (apache)
- Trino (trinodb)
- MinIO Operator + Tenant (min.io)
- Loki (grafana)
- Promtail (grafana)
- Grafana (grafana)
- Prometheus (prometheus-community)
- GPU Operator (nvidia)
- Harbor (goharbor)

**Local Charts (6):**
- explorer
- hl7log-extractor
- hl7-transformer
- hive-metastore
- dcm4chee
- orthanc

### Requirements

1. **Air-gapped deployment**: Worker nodes cannot access internet
2. **Control node access**: Ansible control node (localhost) can access internet and has Scout repository
3. **Staging node connectivity**: Staging node has internet access and can reach k8s cluster API (port 6443)
4. **Backward compatibility**: Non-air-gapped deployments must continue working
5. **Operational simplicity**: Minimize maintenance overhead for both infrastructure and deployment operations
6. **Developer experience**: Minimize specialized knowledge required to add new Helm charts

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
                      │            │
          Port 443    │            │ Port 6443
        (Harbor HTTPS)│            │ (k8s API)
                      │            │
                ┌─────▼────────────▼─────┐
                │  Air-Gapped Cluster    │
                │  - No Internet         │
                │  - API Accessible      │
                │  - Harbor Mirrors      │
                └────────────────────────┘
```

**Key Network Characteristics:**
- Worker nodes: **No internet access** (true air-gap)
- k8s API server: **Accessible from staging node** (permeable air-gap)
- Harbor registry: **Accessible from cluster nodes** (port 443)
- Control node (Ansible): **Has internet access** and kubeconfig

This is a **permeable air-gap**: the cluster is isolated from the internet but not from the staging node's k8s API access.

### Constraints

- Harbor container registry deployed on staging node
- Harbor ChartMuseum deprecated; OCI registry support is current standard
- Most public Helm charts use HTTP-based repositories, not OCI registries
- Staging node has kubeconfig access to air-gapped cluster's k8s API
- No specific requirement for `helm rollback` or `helm history` features
- Temporal workflow relies on manually waiting for schema migration job (already implemented)

## Decision

**We will use remote Helm deployment via kubeconfig for ALL Helm deployments in air-gapped environments.**

### Implementation Pattern

1. **Control/Staging node** (with internet access and kubeconfig):
   - Adds Helm repositories for public charts (internet access available)
   - Deploys charts using `kubernetes.core.helm` module with remote kubeconfig
   - Helm communicates with cluster's k8s API server (port 6443)
   - Charts are rendered server-side with access to cluster's actual API versions

2. **Air-gapped cluster**:
   - Receives Helm release metadata and rendered manifests from Helm via k8s API
   - Pulls container images through Harbor registry mirrors (already configured)
   - Full Helm release management available (rollback, history, hooks)

3. **Mode selection**:
   - `use_staging_node: false` → Use `kubernetes.core.helm` from cluster nodes
   - `use_staging_node: true` → Use `kubernetes.core.helm` from control/staging node with remote kubeconfig

## Alternatives Considered

### Maintenance Cost Metrics

Each alternative is evaluated on these maintenance dimensions:

| Dimension | Description | Impact |
|-----------|-------------|--------|
| **Initial Setup Complexity** | Infrastructure provisioning and configuration | One-time cost |
| **Chart Addition Complexity** | Steps required when adding a new Helm chart | Recurring cost |
| **Chart Upgrade Complexity** | Steps required when upgrading chart versions | Recurring cost |
| **Debugging Complexity** | Effort to diagnose deployment failures | Incident-based cost |
| **Operational Overhead** | Ongoing maintenance of deployment infrastructure | Continuous cost |
| **Developer Knowledge Required** | Specialized knowledge needed to use the system | Training/onboarding cost |

### Alternative 1: OCI Registry for All Charts

**How it works**: Cache all Helm charts in Harbor's OCI registry, deploy from Harbor

**Architecture:**
```
Control Node → (downloads charts) → Staging Node Harbor OCI Registry → Cluster (helm install)
```

**Maintenance Costs:**

| Dimension | Cost | Details |
|-----------|------|---------|
| Initial Setup | **High** | Configure Harbor OCI support, create cache automation |
| Chart Addition | **High** | Must `helm pull` + `helm push` for each chart version |
| Chart Upgrade | **High** | Manual sync of new chart versions to Harbor |
| Debugging | **Medium** | Standard Helm errors, but cache staleness adds complexity |
| Operational Overhead | **High** | Continuous chart version synchronization (12 public charts) |
| Developer Knowledge | **Medium** | Must understand OCI registry and cache management |

**Pros:**
- ✅ Single source of truth for both container images and charts
- ✅ Retains Helm release management (rollback, history, upgrades)
- ✅ Helm hooks execute correctly
- ✅ True air-gap (no external dependencies during deployment)

**Cons:**
- ❌ **Critical blocker**: Most public charts are NOT available as OCI artifacts
  - Would require staging node to run `helm pull` → `helm push` for each chart
  - 12 public charts × multiple versions = ongoing synchronization burden
- ❌ **Operational overhead**: Chart version synchronization, cache staleness monitoring
- ❌ **Complexity**: Harbor OCI support is newer, less mature than HTTP-based repos
- ❌ **Maintenance burden**: Every chart upgrade requires manual intervention

**Total Maintenance Cost**: **Very High** (ongoing operational burden)

---

### Alternative 2: Hybrid Approach (OCI for Local, Rendering for Public)

**How it works**:
- Public charts → Local rendering (helm template)
- Local charts → GitHub Container Registry (OCI)

**Architecture:**
```
Public: Control Node → helm template → kubectl apply → Cluster
Local:  Control Node → GHCR OCI → Cluster (helm install)
```

**Maintenance Costs:**

| Dimension | Cost | Details |
|-----------|------|---------|
| Initial Setup | **High** | Configure both rendering pipeline and OCI publishing |
| Chart Addition | **High** | Different process for public vs local charts |
| Chart Upgrade | **Medium** | Public: simple; Local: requires CI/CD changes |
| Debugging | **High** | Two failure modes: rendering errors vs OCI pull failures |
| Operational Overhead | **Medium** | Maintain two deployment paths |
| Developer Knowledge | **High** | Must understand both rendering and OCI workflows |

**Pros:**
- ✅ Avoids public chart caching complexity
- ✅ Retains Helm features for Scout-controlled charts
- ✅ Natural CI/CD integration for local charts

**Cons:**
- ❌ **Inconsistent patterns**: Two different deployment methods to maintain
- ❌ **Limited benefit**: Our local charts are simple, don't use advanced Helm features
- ❌ **Added complexity**: CI/CD must package and push local charts to GHCR
- ❌ **Cognitive burden**: Developers must know which workflow applies to which chart
- ❌ **Testing burden**: Must test both deployment paths

**Total Maintenance Cost**: **High** (dual-path complexity)

---

### Alternative 3: Local Rendering for All Charts

**How it works**: Render all charts on localhost using `helm template`, apply manifests with `kubectl`

**Architecture:**
```
Control Node → helm template → rendered YAML → kubectl apply → Cluster
```

**Maintenance Costs:**

| Dimension | Cost | Details |
|-----------|------|---------|
| Initial Setup | **Medium** | Create helm_renderer role, refactor 12 playbooks |
| Chart Addition | **High** | Must discover and specify required API versions |
| Chart Upgrade | **High** | Must re-validate API versions on each upgrade |
| Debugging | **Very High** | Cryptic template errors, no Helm release context |
| Operational Overhead | **Low** | No infrastructure to maintain (once built) |
| Developer Knowledge | **Very High** | Must understand Helm templating, API version discovery |

**Pros:**
- ✅ **Universal compatibility**: Works with HTTP repos, OCI, local paths
- ✅ **No chart caching infrastructure**: Harbor only proxies images, not charts
- ✅ **Leverages existing access**: Control node already has internet access
- ✅ **Backward compatible**: Easy to toggle via `use_staging_node` flag
- ✅ **Proven pattern**: Many air-gapped deployments use this approach
- ✅ **True air-gap**: No external dependencies during deployment

**Cons:**
- ❌ **API versions parameter required**: Charts checking API availability fail without explicit `--api-versions` flags
  - Example: cass-operator requires `helm_chart_api_versions: ["cert-manager.io/v1"]`
  - **Discovery process**: Trial-and-error, inspect templates, read chart docs
  - **Hidden requirement**: Not obvious which charts need this
- ❌ **Lost Helm features**: No rollback, no release history, no `--reuse-values`
- ❌ **Helm hooks don't execute**: Pre-install, post-upgrade hooks skipped
- ❌ **No atomic deployments**: `helm install --atomic` rollback on failure unavailable
- ❌ **Upgrade complexity**: Can't use `helm upgrade --reuse-values`
- ❌ **Debugging difficulty**: Generic YAML validation errors, no Helm context
- ❌ **CRD upgrade limitations**: Old CRD versions remain (additive only, not removed)
- ❌ **Developer experience**: Every new chart requires investigation:
  1. Try rendering locally
  2. Encounter cryptic error
  3. Search chart templates for `.Capabilities.APIVersions.Has`
  4. Determine required CRD API versions
  5. Add `helm_chart_api_versions` parameter
  6. Test again
- ❌ **Maintenance burden**: Chart updates may change API requirements
- ❌ **Documentation doesn't fully help**: Even with comprehensive docs, developers must actively debug each chart

**Total Maintenance Cost**: **Very High** (ongoing per-chart investigation burden)

**Real-World Example:**

```yaml
# What developers see (orchestrator.yaml):
- name: Deploy cass-operator
  vars:
    helm_chart_name: cass-operator
    helm_chart_ref: k8ssandra/cass-operator
    helm_chart_version: '~0.55.2'
    helm_chart_namespace: temporal
    helm_repo_name: k8ssandra
    helm_repo_url: https://helm.k8ssandra.io/stable
    # This parameter is NOT obvious - requires investigation:
    helm_chart_api_versions:
      - cert-manager.io/v1  # How do developers know they need this?
```

**Failure Mode (without API versions):**
```
TASK [Render Helm chart on localhost] *******************************************
fatal: [localhost]: FAILED! => {
  "msg": "Error: template: cass-operator/templates/webhooks.yaml:5:10:
         executing \"cass-operator/templates/webhooks.yaml\" at
         <.Capabilities.APIVersions.Has \"cert-manager.io/v1\">:
         wrong type for value; expected bool; got string"
}
```

This error message is **non-obvious** to developers unfamiliar with Helm internals.

---

### Alternative 4: Remote Helm Deployment via Kubeconfig (RECOMMENDED)

**How it works**: Deploy Helm charts from control/staging node using remote kubeconfig to air-gapped cluster

**Architecture:**
```
Control Node (internet + kubeconfig) → k8s API (port 6443) → Cluster
                                             ↓
                                    (Helm queries API versions)
                                             ↓
                                    (Helm installs with full context)
```

**Maintenance Costs:**

| Dimension | Cost | Details |
|-----------|------|---------|
| Initial Setup | **Very Low** | Modify delegation logic only (kubeconfig already exists) |
| Chart Addition | **Very Low** | Standard Helm workflow, no special parameters |
| Chart Upgrade | **Very Low** | Standard `helm upgrade`, no investigation needed |
| Debugging | **Low** | Standard Helm error messages with full context |
| Operational Overhead | **Very Low** | No additional infrastructure to maintain |
| Developer Knowledge | **Very Low** | Standard Helm knowledge, no specialization |

**Pros:**
- ✅ **Full Helm features**: rollback, history, hooks, atomic installs, `--reuse-values`
- ✅ **No API versions parameter needed**: Helm queries cluster API server directly
- ✅ **Simpler mental model**: "Helm from remote machine" (standard pattern)
- ✅ **Better error messages**: Helm validates against actual cluster state, provides context-aware errors
- ✅ **Easier to maintain**: Standard Helm workflow for all charts
- ✅ **Consistent deployment**: Same method for air-gapped and non-air-gapped (only delegation differs)
- ✅ **Developer-friendly**: No specialized knowledge required beyond basic Helm
- ✅ **Debugging simplicity**: `helm list`, `helm status`, `helm get values` all work
- ✅ **Release metadata**: Full audit trail via Helm release history
- ✅ **Upgrade simplicity**: `helm upgrade --reuse-values` just works
- ✅ **Hook execution**: Pre-install, post-upgrade hooks execute correctly
- ✅ **CRD management**: Helm handles CRD upgrades properly
- ✅ **Leverages existing infrastructure**: Kubeconfig already exists for kubectl operations

**Cons:**
- ⚠️ **Requires k8s API port open**: Staging node → cluster (6443)
  - **Mitigation**: Already required for Harbor setup and kubectl operations
- ⚠️ **Network dependency**: Deployment fails if API unreachable
  - **Mitigation**: If k8s API is unreachable, cluster is already inoperable
- ⚠️ **Slightly slower**: Helm makes multiple API calls vs single kubectl apply
  - **Impact**: Negligible (seconds vs minutes of deployment time)
- ⚠️ **Not 100% air-gapped**: Requires k8s API accessibility during deployment
  - **Assessment**: Scout's permeable air-gap model accepts this tradeoff

**Total Maintenance Cost**: **Very Low** (standard Helm with minimal customization)

**Real-World Example:**

```yaml
# What developers see (orchestrator.yaml) with Alternative 4:
- name: Deploy cass-operator
  delegate_to: localhost
  kubernetes.core.helm:
    name: cass-operator
    chart_ref: k8ssandra/cass-operator
    chart_version: '~0.55.2'
    release_namespace: temporal
    create_namespace: true
    kubeconfig: "{{ airgapped_cluster_kubeconfig }}"
    # No helm_chart_api_versions needed!
    # No special investigation required!
    # Just standard Helm parameters!
```

**Success Mode (with remote kubeconfig):**
- Helm queries cluster: "Do you have cert-manager.io/v1?"
- Cluster responds: "Yes, installed by cert-manager chart"
- Helm renders templates with correct API context
- Deployment succeeds without manual intervention

---

## Comparative Analysis

### Maintenance Cost Summary

| Alternative | Initial Setup | Per-Chart Cost | Ongoing Ops | Developer Burden | **Total** |
|-------------|---------------|----------------|-------------|------------------|-----------|
| **Alt 1: OCI Registry** | High | High | High | Medium | **Very High** |
| **Alt 2: Hybrid** | High | High | Medium | High | **High** |
| **Alt 3: Local Rendering** | Medium | **Very High** | Low | **Very High** | **Very High** |
| **Alt 4: Remote Helm** | **Very Low** | **Very Low** | **Very Low** | **Very Low** | **Very Low** |

### Feature Comparison

| Feature | Alt 1: OCI | Alt 2: Hybrid | Alt 3: Rendering | Alt 4: Remote Helm |
|---------|-----------|---------------|------------------|-------------------|
| Helm rollback | ✅ | ✅ (local only) | ❌ | ✅ |
| Helm history | ✅ | ✅ (local only) | ❌ | ✅ |
| Helm hooks | ✅ | ✅ (local only) | ❌ | ✅ |
| Atomic installs | ✅ | ✅ (local only) | ❌ | ✅ |
| `--reuse-values` | ✅ | ✅ (local only) | ❌ | ✅ |
| API version auto-discovery | ✅ | ✅ (local only) | ❌ | ✅ |
| Standard Helm errors | ✅ | ✅ (local only) | ❌ | ✅ |
| Release metadata | ✅ | ✅ (local only) | ❌ | ✅ |
| No API versions parameter | ✅ | ✅ (local only) | ❌ | ✅ |
| Chart cache maintenance | ❌ | ❌ (public) | ✅ | ✅ |
| True 100% air-gap | ✅ | ✅ | ✅ | ⚠️ (needs API) |
| Consistent patterns | ✅ | ❌ | ⚠️ | ✅ |
| Developer-friendly | ⚠️ | ❌ | ❌ | ✅ |

### Air-Gap Model Alignment

Scout's air-gap model is **permeable**:
- Worker nodes: **Isolated** from internet (true air-gap)
- k8s API: **Accessible** from staging node (permeable)
- Harbor: **Accessible** from cluster (permeable)

**Alignment Assessment:**

| Alternative | Alignment | Rationale |
|-------------|-----------|-----------|
| Alt 1: OCI Registry | Over-engineered | Solves for 100% air-gap Scout doesn't need |
| Alt 2: Hybrid | Poor | Complexity doesn't match requirements |
| Alt 3: Local Rendering | Over-engineered | Solves for 100% air-gap Scout doesn't need |
| Alt 4: Remote Helm | **Excellent** | Perfect fit for permeable air-gap model |

## Rationale

### Why Remote Helm Deployment Wins

1. **Permeable air-gap reality**:
   - Staging node already has k8s API access (confirmed)
   - Port 6443 already open for Harbor setup, kubectl operations
   - No additional network requirements beyond existing infrastructure

2. **Maintenance burden elimination**:
   - **No API versions discovery**: Helm queries cluster directly
   - **No chart investigation**: Standard Helm workflow for all charts
   - **No version drift tracking**: Helm handles API compatibility automatically
   - **No documentation burden**: Developers use standard Helm knowledge

3. **Developer experience**:
   - **Familiar patterns**: Standard Helm workflow everyone knows
   - **Better errors**: Context-aware messages from Helm
   - **Faster onboarding**: No specialized knowledge required
   - **Confidence**: "It's just Helm" reduces cognitive load

4. **Operational simplicity**:
   - **Minimal infrastructure**: Uses existing kubeconfig
   - **No caching layer**: No Harbor chart management needed
   - **Consistent patterns**: Same Helm workflow everywhere
   - **Standard debugging**: All Helm commands work

5. **Feature preservation**:
   - **Rollback capability**: Available even if not currently used
   - **Audit trail**: Helm release history provides deployment tracking
   - **Hook execution**: Charts work as designed by upstream
   - **Upgrade simplicity**: `helm upgrade` with all features

6. **Implementation simplicity**:
   - **Minimal changes**: Only delegation logic needs modification
   - **Fast implementation**: ~1-2 days vs weeks for alternatives
   - **Lower risk**: Smaller code change surface area
   - **Easy rollback**: Can revert to Alternative 3 if needed

### Trade-offs Accepted

| Trade-off | Impact | Assessment |
|-----------|--------|------------|
| **k8s API dependency** | Deployment fails if API unreachable | Acceptable: API required for cluster operations anyway |
| **Not 100% air-gapped** | k8s API port must be accessible | Acceptable: Scout's permeable model allows this |
| **Network latency** | Helm API calls add slight overhead | Negligible: Seconds in minutes-long deployments |

### Alternative 3 (Current Implementation) - Why It Should Be Replaced

The current local rendering approach (Alternative 3) was chosen based on these assumptions:
1. **Assumption**: "100% air-gap required" → **Reality**: k8s API is accessible (permeable)
2. **Assumption**: "Operational simplicity" → **Reality**: High developer burden (API versions discovery)
3. **Assumption**: "Proven pattern" → **Reality**: Proven for true air-gap, over-engineered for Scout

**Maintenance Cost Realization:**
- Every new chart requires API versions investigation
- Chart upgrades may silently break if API requirements change
- Documentation and error handling add complexity but don't eliminate the core problem
- Developer experience is poor (cryptic errors, trial-and-error debugging)

**The Critical Insight:**
> "If k8s API access exists, why are we rendering charts client-side when Helm can query the API server directly?"

Alternative 4 eliminates the API versions problem entirely by using the cluster's actual API state during rendering.

## Implementation Impact

### Components to Modify

**Minimal changes required:**

1. **Delegation logic**: Update `tasks/deploy_helm_chart.yaml` to delegate to localhost with remote kubeconfig
2. **Kubeconfig handling**: Ensure `airgapped_cluster_kubeconfig` variable is available
3. **Remove complexity**: Delete `helm_renderer` role (no longer needed)
4. **Simplify playbooks**: Revert to standard `kubernetes.core.helm` calls

### Estimated Effort

- **Architecture decision**: 1 day (this document)
- **Implementation**: 1-2 days (simplification, mostly deletions)
- **Testing**: 3-5 days (validate all 18 charts in both modes)
- **Documentation update**: 1 day
- **Total**: ~1 week (vs 2+ weeks for Alternative 3)

### Migration Path

**From Alternative 3 (current) to Alternative 4:**

1. **Phase 1**: Implement remote Helm deployment with delegation
2. **Phase 2**: Test all 18 charts in air-gapped mode
3. **Phase 3**: Remove `helm_renderer` role and related complexity
4. **Phase 4**: Update documentation

**Rollback plan**: Keep Alternative 3 code in git history if remote Helm encounters unforeseen issues.

## Validation

### Success Criteria

- [ ] **Remote Helm deployment**: All 18 charts deploy successfully using remote kubeconfig
- [ ] **No API versions parameters**: Confirm no charts require manual API version specification
- [ ] **Regression testing**: Non-air-gapped deployments work correctly
- [ ] **Service validation**: All services start and pass health checks in both modes
- [ ] **Developer experience**: New chart additions require no special investigation
- [ ] **Performance**: Deployment time within 10% of current baseline
- [ ] **Debugging**: Helm commands (`helm list`, `helm status`) work correctly

### Testing Strategy

1. **Prototype**: Deploy single chart (cert-manager) using remote kubeconfig from staging node
2. **Validate**: Confirm Helm queries API versions correctly without manual specification
3. **Expand**: Deploy all 18 charts in air-gapped test environment
4. **Compare**: Deploy same charts using Alternative 3 (local rendering) for comparison
5. **Document**: Record deployment times, errors, developer experience differences
6. **Decision**: Confirm Alternative 4 superiority in maintenance and usability

## References

### External References

- [Helm Documentation](https://helm.sh/docs/) - Official Helm documentation
- [Air-Gapped Kubernetes Best Practices](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/) - Kubernetes documentation
- [Harbor Documentation](https://goharbor.io/docs/) - Harbor registry documentation
- [Ansible kubernetes.core.helm module](https://docs.ansible.com/ansible/latest/collections/kubernetes/core/helm_module.html) - Ansible Helm module docs

### Internal References

- `staging-node-architecture.md` - Network topology and Harbor setup
- `air-gapped-helm-test-plan.md` - Testing procedures
- `air-gapped-helm-architecture-decision.md` - Previous ADR (superseded)

### Helm Hook Analysis

From `temporal/temporal:0.62.0` chart inspection:

```bash
$ grep -r "helm.sh/hook" temporal/
temporal/templates/serviceaccount.yaml:    helm.sh/hook: pre-install, pre-upgrade
temporal/templates/serviceaccount.yaml:    helm.sh/hook-weight: "-10"
# Plus test hooks in subchart dependencies (grafana, prometheus, elasticsearch)
```

**Analysis**:
- ServiceAccount pre-install hook ensures SA exists before pods
- Not functionally critical but improves deployment reliability
- Test hooks only run with `helm test` command (not used in production)
- Schema migration job is NOT a hook (regular Job created by chart)
- **Alternative 4 preserves hook execution** (Alternative 3 does not)

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2025-10-14 | Initial ADR created with 4 alternatives | Claude Code |
| 2025-10-14 | Added maintenance cost analysis | Claude Code |
| 2025-10-14 | Documented permeable air-gap model | Claude Code |
