# helm_renderer

Ansible role for deploying Helm charts with support for both standard and
air-gapped Kubernetes environments.

## Description

This role provides a unified interface for deploying Helm charts in Scout
deployments. It automatically switches between two deployment strategies based
on the `use_staging_node` inventory variable:

- **Non-air-gapped mode** (`use_staging_node: false`): Uses the standard
  `kubernetes.core.helm` module for direct Helm deployment
- **Air-gapped mode** (`use_staging_node: true`): Renders charts on the
  Ansible control node (localhost) using `helm template`, then applies the
  rendered manifests to the cluster

## Requirements

### Non-air-gapped mode
- Ansible 2.14+
- `kubernetes.core` collection 2.4.0+
- Network connectivity from control node to Kubernetes cluster

### Air-gapped mode
- All non-air-gapped requirements, plus:
- Helm 3.12+ installed on the Ansible control node (localhost)
- Network connectivity from localhost to public Helm repositories
  (for public charts)

## Role Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `helm_chart_name` | Helm release name | `cert-manager` |
| `helm_chart_ref` | Chart reference (repo/chart or local path) | `jetstack/cert-manager` or `/path/to/chart` |
| `helm_chart_namespace` | Target Kubernetes namespace | `cert-manager` |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `helm_chart_version` | `null` | Chart version (for repository charts) |
| `helm_chart_create_namespace` | `true` | Create namespace if it doesn't exist |
| `helm_chart_wait` | `true` | Wait for resources to be ready |
| `helm_chart_timeout` | `"5m"` | Timeout for wait operations |
| `helm_chart_values` | `{}` | Values dictionary for chart customization |
| `helm_chart_values_files` | `[]` | List of values file paths |
| `helm_chart_api_versions` | `[]` | API versions for chart templating (see below) |
| `helm_chart_cleanup_on_failure` | `false` | Automatically delete namespace if deployment fails |
| `helm_chart_skip_apply` | `false` | Render only, don't apply (testing) |
| `helm_chart_skip_cleanup` | `false` | Keep rendered files (debugging) |
| `helm_repo_name` | `null` | Helm repository name (for repository charts) |
| `helm_repo_url` | `null` | Helm repository URL (for repository charts) |
| `use_staging_node` | `false` | Enable air-gapped deployment mode |

## Dependencies

None.

## Example Playbook

### Public Chart Deployment

```yaml
- hosts: server
  tasks:
    - name: Deploy cert-manager
      ansible.builtin.include_role:
        name: helm_renderer
      vars:
        helm_chart_name: cert-manager
        helm_chart_ref: jetstack/cert-manager
        helm_chart_namespace: cert-manager
        helm_chart_version: "v1.14.2"
        helm_repo_name: jetstack
        helm_repo_url: https://charts.jetstack.io
        helm_chart_create_namespace: true
        helm_chart_wait: true
        helm_chart_timeout: 5m
        helm_chart_values:
          installCRDs: true
```

### Local Chart Deployment

```yaml
- hosts: server
  tasks:
    - name: Deploy explorer
      ansible.builtin.include_role:
        name: helm_renderer
      vars:
        helm_chart_name: explorer
        helm_chart_ref: "{{ scout_repo_dir }}/helm/explorer"
        helm_chart_namespace: explorer
        helm_chart_create_namespace: true
        helm_chart_wait: true
        helm_chart_timeout: 5m
        helm_chart_values:
          image:
            repository: ghcr.io/washu-tag/explorer
```

### Using the Wrapper Task

For simplified usage across multiple playbooks, use the wrapper task:

```yaml
- hosts: server
  tasks:
    - name: Deploy cert-manager
      ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
      vars:
        helm_chart_name: cert-manager
        helm_chart_ref: jetstack/cert-manager
        helm_chart_namespace: cert-manager
        helm_repo_name: jetstack
        helm_repo_url: https://charts.jetstack.io
        helm_chart_values:
          installCRDs: true
```

## Air-Gapped Deployment

To enable air-gapped mode, set `use_staging_node: true` in your inventory:

```yaml
k3s_cluster:
  vars:
    use_staging_node: true
```

The role will automatically:
1. Verify Helm is installed on localhost
2. Add required Helm repositories on localhost
3. Render charts using `helm template`
4. Apply rendered manifests to the cluster
5. Clean up temporary files

### API Versions for Air-Gapped Deployments

**Critical for charts with CRD dependencies!**

Some Helm charts check for CRD availability during rendering using template logic like `{{ .Capabilities.APIVersions.Has "cert-manager.io/v1" }}`. In air-gapped mode, these checks fail because `helm template` doesn't connect to a cluster, causing deployment to fail with cryptic errors.

#### When Do You Need This?

You need to specify `helm_chart_api_versions` when deploying charts that:
- Use admission webhooks (almost always requires cert-manager)
- Reference CRDs from other charts in their templates
- Have conditional logic based on API availability

#### How to Identify the Need

**Symptom 1 - Deployment Failure:**
```
Error: execution error at (cass-operator/templates/webhook-service.yaml:6:6):
cass-operator webhooks require cert-manager to be installed in the cluster
```

**Symptom 2 - Test Rendering Locally:**
```bash
$ helm template test k8ssandra/cass-operator --version 0.55.2 --namespace temporal
Error: execution error ... requires cert-manager ...
```

#### How to Find Required API Versions

**Method 1 - Test with Common API Versions:**
```bash
# Try adding cert-manager (most common dependency)
$ helm template test repo/chart \
    --version X.Y.Z \
    --namespace test \
    --api-versions cert-manager.io/v1

# If successful, you found it!
```

**Method 2 - Inspect Chart Templates:**
```bash
# Look for Capabilities checks in the chart
$ helm show all repo/chart | grep -A 2 "Capabilities.APIVersions"

# Example output:
{{ if .Capabilities.APIVersions.Has "cert-manager.io/v1" }}
  # This means you need: cert-manager.io/v1
```

**Method 3 - Check Dependency CRDs:**
```bash
# Download and inspect the dependency chart
$ helm pull jetstack/cert-manager --untar
$ grep "apiVersion:" cert-manager/crds/*.yaml | head -5

# Output shows: apiVersion: apiextensions.k8s.io/v1
# The CRD itself uses apiextensions, but the CRD group is cert-manager.io/v1
```

**Method 4 - Read Chart Documentation:**
- Check the chart's README or values.yaml for dependency requirements
- Look for mentions of "cert-manager", "webhooks", or "CRDs"

#### Common API Versions

| API Version | Used By | When Needed |
|-------------|---------|-------------|
| `cert-manager.io/v1` | Admission webhooks, TLS automation | Charts with MutatingWebhookConfiguration or ValidatingWebhookConfiguration |
| `monitoring.coreos.com/v1` | Prometheus monitoring | Charts creating ServiceMonitor resources |
| `snapshot.storage.k8s.io/v1` | Volume snapshots | Charts using VolumeSnapshot features |
| `networking.k8s.io/v1` | Network policies | Charts with NetworkPolicy resources |
| `policy/v1` | Pod disruption budgets | Charts using PodDisruptionBudget |

#### Examples

**cass-operator (requires cert-manager):**
```yaml
- name: Deploy cass-operator
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: cass-operator
    helm_chart_ref: k8ssandra/cass-operator
    helm_chart_version: '~0.55.2'
    helm_chart_namespace: temporal
    helm_repo_name: k8ssandra
    helm_repo_url: https://helm.k8ssandra.io/stable
    # Required: cass-operator uses admission webhooks
    helm_chart_api_versions:
      - cert-manager.io/v1
```

**Chart with multiple dependencies:**
```yaml
- name: Deploy monitoring stack
  ansible.builtin.include_role:
    name: helm_renderer
  vars:
    helm_chart_name: monitoring
    helm_chart_ref: prometheus-community/kube-prometheus-stack
    helm_chart_namespace: monitoring
    helm_repo_name: prometheus-community
    helm_repo_url: https://prometheus-community.github.io/helm-charts
    helm_chart_api_versions:
      - cert-manager.io/v1           # For webhook TLS
      - monitoring.coreos.com/v1     # For ServiceMonitor CRDs
```

#### What If You Don't Set This?

If a chart requires API versions but you don't provide them:

1. **In non-air-gapped mode**: No impact (Helm queries the cluster directly)
2. **In air-gapped mode**: Deployment will fail with errors like:
   - `"requires cert-manager to be installed"`
   - `"execution error at (chart/template.yaml:X:Y)"`
   - Template rendering failures with cryptic messages

**The fix**: Add the missing API version(s) to `helm_chart_api_versions`

## Error Handling

The role includes robust error handling for partial deployment failures:

### Partial Manifest Apply Failures

If some manifests fail to apply, the role:
1. Continues applying remaining manifests (doesn't fail fast)
2. Collects all failures
3. Reports which manifests failed and why
4. Optionally cleans up the namespace (if `helm_chart_cleanup_on_failure: true`)

**Example with cleanup disabled (default)**:
```yaml
- name: Deploy chart with manual cleanup
  include_role:
    name: helm_renderer
  vars:
    helm_chart_cleanup_on_failure: false  # Leave resources for inspection
```

On failure, you'll see:
```
Failed to apply 2 manifest(s) for my-app:
  - deployment.yaml: error message here
  - service.yaml: error message here

Successful manifests remain applied. To clean up:
  kubectl delete namespace my-namespace
```

**Example with automatic cleanup**:
```yaml
- name: Deploy chart with automatic cleanup
  include_role:
    name: helm_renderer
  vars:
    helm_chart_cleanup_on_failure: true  # Auto-delete on failure
```

On failure, the namespace is automatically deleted, leaving a clean state.

## Testing

Run Molecule tests:

```bash
cd ansible/roles/helm_renderer
uvx molecule test
```

The Molecule tests include:
- **Test 1**: Render nginx chart and validate YAML syntax
- **Test 2**: Render cert-manager chart with CRD detection
- **Test 3**: Variable validation (required variables enforcement)
- **Test 4**: Local chart rendering (if `$SCOUT_REPO_DIR` set)
- **Verification**: Role structure, error handling, and documentation checks

**Test Results**:
- ✅ Syntax check
- ✅ Converge (17 tasks, 4 changed, 2 skipped, 1 rescued)
- ✅ Idempotence (7 tasks, all idempotent)
- ✅ Verify (9 validation checks)

Note: Full integration tests with cluster deployment require a live Kubernetes cluster.

## License

MIT

## Author Information

Scout Platform Team - Washington University School of Medicine - TAG
