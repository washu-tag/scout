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
| `helm_chart_cleanup_on_failure` | `false` | Automatically delete namespace if deployment fails |
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
- **Rendering validation**: Verify charts render to valid YAML
- **CRD detection**: Ensure CRDs are included in rendered output
- **Variable validation**: Check required variables are enforced
- **Error handling**: Verify failure scenarios are handled correctly

Note: Full integration tests require a live Kubernetes cluster.

## License

MIT

## Author Information

Scout Platform Team - Washington University School of Medicine - TAG
