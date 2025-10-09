# helm_renderer Role - Usage Examples

This document provides practical examples of using the `helm_renderer` role
and `deploy_helm_chart.yaml` wrapper task.

## Table of Contents

1. [Basic Usage](#basic-usage)
2. [Public Charts](#public-charts)
3. [Local Charts](#local-charts)
4. [Advanced Configurations](#advanced-configurations)
5. [Migration Examples](#migration-examples)

---

## Basic Usage

### Minimal Example

```yaml
- name: Deploy a chart
  ansible.builtin.include_role:
    name: helm_renderer
  vars:
    helm_chart_name: my-app
    helm_chart_ref: stable/my-app
    helm_chart_namespace: apps
```

### Using the Wrapper Task

```yaml
- name: Deploy a chart
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: my-app
    helm_chart_ref: stable/my-app
    helm_chart_namespace: apps
```

---

## Public Charts

### Example 1: cert-manager (from implementation plan)

**Before (current approach):**

```yaml
- name: Add cert-manager Helm repository
  kubernetes.core.helm_repository:
    name: jetstack
    repo_url: https://charts.jetstack.io

- name: Install cert-manager
  kubernetes.core.helm:
    name: cert-manager
    chart_ref: jetstack/cert-manager
    release_namespace: cert-manager
    create_namespace: true
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: 5m
    atomic: true
    values:
      installCRDs: true
```

**After (using helm_renderer):**

```yaml
- name: Deploy cert-manager
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: cert-manager
    helm_chart_ref: jetstack/cert-manager
    helm_chart_namespace: cert-manager
    helm_repo_name: jetstack
    helm_repo_url: https://charts.jetstack.io
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 5m
    helm_chart_values:
      installCRDs: true
```

### Example 2: JupyterHub with Complex Values

**Using the wrapper task:**

```yaml
- name: Set JupyterHub values
  ansible.builtin.set_fact:
    jupyter_values:
      hub:
        baseUrl: /jupyter
        db:
          type: postgres
          url: postgresql://{{ jupyter_postgres_user }}:{{ jupyter_postgres_password }}@postgresql-cluster-rw.{{ postgres_cluster_namespace }}:5432/jupyter
      proxy:
        service:
          type: ClusterIP
      singleuser:
        image:
          name: ghcr.io/washu-tag/pyspark-notebook
          tag: latest

- name: Deploy JupyterHub
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: jupyter
    helm_chart_ref: jupyterhub/jupyterhub
    helm_chart_version: '~4.2.0'
    helm_chart_namespace: jupyter
    helm_repo_name: jupyterhub
    helm_repo_url: https://raw.githubusercontent.com/jupyterhub/helm-chart/refs/heads/gh-pages/
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 10m
    helm_chart_values: '{{ jupyter_values }}'
```

### Example 3: MinIO Operator

```yaml
- name: Deploy MinIO Operator
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: minio-operator
    helm_chart_ref: minio-operator/operator
    helm_chart_version: '{{ minio_version | default("~7.1.0") }}'
    helm_chart_namespace: minio-operator
    helm_repo_name: minio-operator
    helm_repo_url: https://operator.min.io
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 1m
```

---

## Local Charts

### Example 4: Scout Explorer

**Before (current approach):**

```yaml
- name: Install explorer using Helm
  kubernetes.core.helm:
    name: explorer
    release_namespace: explorer
    create_namespace: true
    chart_ref: '{{ scout_repo_dir }}/helm/explorer'
    state: present
    wait: true
    wait_timeout: 5m
    atomic: true
    values_files:
      - '{{ scout_repo_dir }}/helm/explorer/values.yaml'
    values:
      image:
        repository: '{{ explorer_image | default(omit) }}'
        tag: '{{ explorer_image_tag | default(omit) }}'
    kubeconfig: '{{ local_kubeconfig_yaml }}'
  delegate_to: localhost
```

**After (using helm_renderer):**

```yaml
- name: Deploy explorer
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: explorer
    helm_chart_ref: '{{ scout_repo_dir }}/helm/explorer'
    helm_chart_namespace: explorer
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 5m
    helm_chart_values_files:
      - '{{ scout_repo_dir }}/helm/explorer/values.yaml'
    helm_chart_values:
      image:
        repository: '{{ explorer_image | default(omit) }}'
        tag: '{{ explorer_image_tag | default(omit) }}'
```

### Example 5: HL7 Extractor (with template values)

```yaml
- name: Load HL7Log Extractor values from template
  ansible.builtin.set_fact:
    hl7log_extractor_values: "{{ lookup('template', 'templates/hl7log-extractor.values.yaml.j2') | from_yaml }}"

- name: Deploy HL7Log Extractor
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: hl7log-extractor
    helm_chart_ref: '{{ scout_repo_dir }}/helm/extractor/hl7log-extractor'
    helm_chart_namespace: '{{ extractor_namespace }}'
    helm_chart_create_namespace: false
    helm_chart_wait: true
    helm_chart_timeout: 5m
    helm_chart_values: '{{ hl7log_extractor_values }}'
```

---

## Advanced Configurations

### Example 6: Chart with Multiple Values Files

```yaml
- name: Deploy Superset with multiple values
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: superset
    helm_chart_ref: superset/superset
    helm_chart_version: '0.14.2'
    helm_chart_namespace: superset
    helm_repo_name: superset
    helm_repo_url: https://apache.github.io/superset
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 15m
    helm_chart_values_files:
      - '{{ scout_repo_dir }}/ansible/playbooks/vars/superset.base.yaml'
      - '{{ scout_repo_dir }}/ansible/playbooks/vars/superset.{{ env }}.yaml'
    helm_chart_values:
      # Override specific values
      image:
        repository: ghcr.io/washu-tag/superset
        tag: 4.1.2
```

### Example 7: Conditional Deployment with Version Selection

```yaml
- name: Set chart version based on environment
  ansible.builtin.set_fact:
    temporal_version: >-
      {{ '~0.62.0' if env == 'production' else '~0.61.0' }}

- name: Deploy Temporal
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: temporal
    helm_chart_ref: temporal/temporal
    helm_chart_version: '{{ temporal_version }}'
    helm_chart_namespace: '{{ temporal_namespace }}'
    helm_repo_name: temporal
    helm_repo_url: https://raw.githubusercontent.com/temporalio/helm-charts/refs/heads/gh-pages/
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 15m
    helm_chart_values:
      server:
        replicaCount: '{{ 3 if env == "production" else 1 }}'
```

### Example 8: No Wait (Fire and Forget)

```yaml
- name: Deploy background service (no wait)
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: background-worker
    helm_chart_ref: local/background-worker
    helm_chart_namespace: workers
    helm_chart_create_namespace: true
    helm_chart_wait: false  # Don't wait for deployment to complete
```

---

## Migration Examples

### Example 9: Temporal (Complex Migration)

**Before:**

```yaml
- name: Add Temporal Helm repository
  kubernetes.core.helm_repository:
    name: temporal
    repo_url: https://raw.githubusercontent.com/temporalio/helm-charts/refs/heads/gh-pages/

- name: Install/Upgrade Temporal Helm chart
  register: temporal_helm_result
  kubernetes.core.helm:
    name: temporal
    chart_ref: temporal/temporal
    chart_version: ~0.62.0
    release_namespace: '{{ temporal_namespace }}'
    create_namespace: true
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: 15m
    atomic: true
    values:
      # ... extensive values ...

- name: Wait for Temporal schema job to complete
  shell: >-
    if kubectl -n {{ temporal_namespace }} get jobs temporal-schema-{{ temporal_helm_result.status.revision }} >/dev/null 2>&1; then
      kubectl -n {{ temporal_namespace }} wait --for=condition=complete --timeout=300s job/temporal-schema-{{ temporal_helm_result.status.revision }}
    fi
  register: temporal_schema
  changed_when: false
```

**After:**

```yaml
- name: Set Temporal chart values
  ansible.builtin.set_fact:
    temporal_chart_values:
      # ... extensive values (same as before) ...

- name: Deploy Temporal
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: temporal
    helm_chart_ref: temporal/temporal
    helm_chart_version: '~0.62.0'
    helm_chart_namespace: '{{ temporal_namespace }}'
    helm_repo_name: temporal
    helm_repo_url: https://raw.githubusercontent.com/temporalio/helm-charts/refs/heads/gh-pages/
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 15m
    helm_chart_values: '{{ temporal_chart_values }}'

- name: Wait for Temporal schema job to complete (air-gapped compatible)
  shell: >-
    JOB_NAME=$(kubectl -n {{ temporal_namespace }} get jobs -l app.kubernetes.io/component=schema -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "");
    if [ -n "$JOB_NAME" ]; then
      kubectl -n {{ temporal_namespace }} wait --for=condition=complete --timeout=300s job/$JOB_NAME;
    fi
  register: temporal_schema
  changed_when: false
```

**Key changes:**
- Values moved to `set_fact` for clarity
- Wrapper task handles repo setup and deployment
- Schema job wait now uses label selector (works in both modes)

---

## Testing the Role

### Test in Non-Air-Gapped Mode

Set in inventory:
```yaml
all:
  vars:
    use_staging_node: false  # or omit entirely
```

Run playbook:
```bash
ansible-playbook -i inventory.yaml playbooks/orchestrator.yaml
```

Expected: Uses standard `kubernetes.core.helm` module

### Test in Air-Gapped Mode

Set in inventory:
```yaml
all:
  vars:
    use_staging_node: true
```

Ensure Helm is installed on localhost:
```bash
helm version
```

Run playbook:
```bash
ansible-playbook -i inventory.airgapped.yaml playbooks/orchestrator.yaml
```

Expected: Renders charts on localhost, applies manifests to cluster

---

## Common Patterns

### Pattern 1: Reusable Chart Variables

```yaml
# In group_vars/all.yaml
common_chart_settings:
  helm_chart_create_namespace: true
  helm_chart_wait: true
  helm_chart_timeout: 5m

# In playbook
- name: Deploy with common settings
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: my-app
    helm_chart_ref: stable/my-app
    helm_chart_namespace: apps
    # Merge common settings
    helm_chart_create_namespace: '{{ common_chart_settings.helm_chart_create_namespace }}'
    helm_chart_wait: '{{ common_chart_settings.helm_chart_wait }}'
    helm_chart_timeout: '{{ common_chart_settings.helm_chart_timeout }}'
```

### Pattern 2: Loop Over Multiple Charts

```yaml
- name: Deploy multiple monitoring charts
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: '{{ item.name }}'
    helm_chart_ref: '{{ item.repo }}/{{ item.name }}'
    helm_chart_namespace: monitoring
    helm_repo_name: '{{ item.repo }}'
    helm_repo_url: '{{ item.repo_url }}'
    helm_chart_values: '{{ item.values | default({}) }}'
  loop:
    - name: grafana
      repo: grafana
      repo_url: https://grafana.github.io/helm-charts
      values:
        ingress:
          enabled: true
    - name: prometheus
      repo: prometheus-community
      repo_url: https://prometheus-community.github.io/helm-charts
      values:
        server:
          persistentVolume:
            enabled: true
```

---

## Troubleshooting

### Issue: "Helm must be installed on the Ansible control node"

**Solution:** Install Helm on localhost:
```bash
brew install helm  # macOS
# or
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### Issue: Chart rendering fails with version not found

**Solution:** Check chart version exists:
```bash
helm search repo <repo-name>/<chart-name> --versions
```

### Issue: Manifests apply but pods don't start

**Check:**
1. Container registry accessibility (Harbor mirrors configured?)
2. Image pull secrets
3. Resource quotas
4. Storage class availability

```bash
kubectl describe pod <pod-name> -n <namespace>
```
