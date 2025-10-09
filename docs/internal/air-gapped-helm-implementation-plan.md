# Air-Gapped Helm Deployment - Implementation Plan

**Status**: Proposed
**Date**: 2025-10-08
**Related Documents**:
- `air-gapped-helm-architecture-decision.md` (Architecture rationale)
- `staging-node-implementation-plan.md` (Harbor container registry)

## Overview

This document provides a detailed implementation plan for deploying Helm charts to air-gapped Kubernetes clusters using local chart rendering. The approach renders charts on the Ansible control node (localhost with internet access) and applies pre-rendered manifests to the air-gapped cluster.

### Architecture Decision Summary

**Selected Strategy**: Local chart rendering for all Helm deployments in air-gapped environments

**Key Points**:
- Render charts with `helm template` on localhost
- Transfer rendered YAML manifests to cluster
- Apply with `kubernetes.core.k8s` Ansible module
- Toggle via `use_staging_node` flag for backward compatibility

## Affected Components

### Helm Chart Inventory

Scout uses **16 Helm charts** across its deployment:

#### Public Charts from External Repositories (10 charts)

| Chart | Repository | Current Playbook | Version Pinned |
|-------|------------|------------------|----------------|
| cert-manager | https://charts.jetstack.io | `orchestrator.yaml` | ~v1.14.0 |
| cass-operator | https://helm.k8ssandra.io/stable | `orchestrator.yaml` | ~0.55.2 |
| Temporal | https://raw.githubusercontent.com/temporalio/helm-charts/.../gh-pages/ | `orchestrator.yaml` | ~0.62.0 |
| JupyterHub | https://raw.githubusercontent.com/jupyterhub/helm-chart/.../gh-pages/ | `jupyter.yaml` | ~4.2.0 |
| Superset | https://apache.github.io/superset | `analytics.yaml` (services/superset.yaml) | 0.14.2 |
| Trino | https://trinodb.github.io/charts | `analytics.yaml` (services/trino.yaml) | ~1.38.0 |
| MinIO Operator | https://operator.min.io | `lake.yaml` (services/minio.yaml) | ~7.1.0 |
| MinIO Tenant | https://operator.min.io | `lake.yaml` (services/minio.yaml) | ~7.1.0 |
| Grafana | https://grafana.github.io/helm-charts | `monitor.yaml` (services/grafana.yaml) | TBD |
| Prometheus | https://prometheus-community.github.io/helm-charts | `monitor.yaml` (services/prometheus.yaml) | TBD |

#### Local Charts from Scout Repository (6 charts)

| Chart | Path | Current Playbook |
|-------|------|------------------|
| explorer | `helm/explorer` | `explorer.yaml` |
| hl7log-extractor | `helm/extractor/hl7log-extractor` | `extractor.yaml` |
| hl7-transformer | `helm/extractor/hl7-transformer` | `extractor.yaml` |
| hive-metastore | `helm/lake/hive-metastore` | `lake.yaml` (services/hive.yaml) |
| dcm4chee | `helm/dcm4chee` | `dcm4chee.yaml` |
| orthanc | `helm/orthanc` | `orthanc.yaml` |

### Ansible Playbooks to Modify (12 playbooks)

1. `ansible/playbooks/orchestrator.yaml` - cert-manager, cass-operator, Temporal
2. `ansible/playbooks/jupyter.yaml` - JupyterHub
3. `ansible/playbooks/analytics.yaml` (includes `services/superset.yaml`, `services/trino.yaml`)
4. `ansible/playbooks/lake.yaml` (includes `services/minio.yaml`, `services/hive.yaml`)
5. `ansible/playbooks/explorer.yaml` - explorer
6. `ansible/playbooks/extractor.yaml` - hl7log-extractor, hl7-transformer
7. `ansible/playbooks/dcm4chee.yaml` - dcm4chee
8. `ansible/playbooks/orthanc.yaml` - orthanc
9. `ansible/playbooks/monitor.yaml` (includes `services/grafana.yaml`, `services/prometheus.yaml`)

**Note**: Some playbooks are composed using `include_tasks` pointing to `services/*.yaml` files.

## Implementation Phases

### Phase 1: Create Reusable Rendering Infrastructure

**Goal**: Build Ansible role/tasks for rendering Helm charts that can be reused across all playbooks

**Deliverables**:
- `ansible/roles/helm_renderer/` role (or reusable tasks)
- Support for both public and local charts
- Conditional logic based on `use_staging_node` flag

#### 1.1: Create Helm Renderer Role Structure

**Directory structure**:
```
ansible/roles/helm_renderer/
├── defaults/
│   └── main.yaml              # Default variables
├── meta/
│   └── main.yaml              # Role metadata
├── tasks/
│   ├── main.yaml              # Entry point
│   ├── render_chart.yaml      # Chart rendering logic
│   └── apply_manifests.yaml   # Apply rendered YAML
└── molecule/
    └── default/
        ├── molecule.yml       # Molecule test config
        ├── converge.yml       # Test playbook
        └── verify.yml         # Verification tests
```

#### 1.2: Define Role Interface

**Role variables** (caller provides these):

```yaml
# Required
helm_chart_name: "cert-manager"              # Helm release name
helm_chart_ref: "jetstack/cert-manager"      # Chart reference (repo/chart or local path)
helm_chart_namespace: "cert-manager"         # Target namespace
helm_chart_values: {}                        # Values dict or values_files list

# Optional
helm_chart_version: "v1.14.2"                # Chart version (for repo charts)
helm_chart_create_namespace: true            # Create namespace if missing
helm_chart_wait: true                        # Wait for resources to be ready
helm_chart_timeout: "5m"                     # Timeout for wait

# For repository charts only
helm_repo_name: "jetstack"                   # Repository name
helm_repo_url: "https://charts.jetstack.io"  # Repository URL

# Internal (computed)
use_staging_node: false                      # Flag from inventory
```

**Role behavior**:

```yaml
# When use_staging_node: false (current behavior)
- Use kubernetes.core.helm module directly
- Chart deployed with full Helm release management

# When use_staging_node: true (air-gapped mode)
- Delegate to localhost
- Add Helm repository (if repo chart)
- Render chart with helm template
- Transfer manifests to remote
- Apply with kubernetes.core.k8s
```

#### 1.3: Implement Rendering Logic

**File**: `ansible/roles/helm_renderer/tasks/render_chart.yaml`

```yaml
---
# Render Helm chart on localhost and apply to remote cluster
# This task file is included when use_staging_node: true

- name: "Ensure Helm is installed on localhost"
  delegate_to: localhost
  ansible.builtin.command: helm version --short
  register: helm_version_check
  failed_when: false
  changed_when: false

- name: "Fail if Helm not available on localhost"
  delegate_to: localhost
  ansible.builtin.fail:
    msg: "Helm must be installed on the Ansible control node (localhost) for air-gapped deployments"
  when: helm_version_check.rc != 0

- name: "Add Helm repository on localhost"
  delegate_to: localhost
  kubernetes.core.helm_repository:
    name: "{{ helm_repo_name }}"
    repo_url: "{{ helm_repo_url }}"
  when:
    - helm_repo_name is defined
    - helm_repo_url is defined

- name: "Create temporary directory for rendered manifests"
  delegate_to: localhost
  ansible.builtin.tempfile:
    state: directory
    prefix: "helm_{{ helm_chart_name }}_"
  register: helm_render_dir

- name: "Render Helm chart on localhost"
  delegate_to: localhost
  ansible.builtin.command:
    cmd: >-
      helm template {{ helm_chart_name }}
      {{ helm_chart_ref }}
      {% if helm_chart_version is defined %}--version {{ helm_chart_version }}{% endif %}
      --namespace {{ helm_chart_namespace }}
      {% if helm_chart_create_namespace | default(false) %}--create-namespace{% endif %}
      {% if helm_chart_values_file is defined %}--values {{ helm_chart_values_file }}{% endif %}
      --output-dir {{ helm_render_dir.path }}
  register: helm_template_result
  changed_when: true

- name: "Write inline values to temporary file"
  delegate_to: localhost
  ansible.builtin.copy:
    content: "{{ helm_chart_values | to_nice_yaml }}"
    dest: "{{ helm_render_dir.path }}/inline-values.yaml"
  when:
    - helm_chart_values is defined
    - helm_chart_values | length > 0
    - helm_chart_values_file is not defined

- name: "Re-render with inline values"
  delegate_to: localhost
  ansible.builtin.command:
    cmd: >-
      helm template {{ helm_chart_name }}
      {{ helm_chart_ref }}
      {% if helm_chart_version is defined %}--version {{ helm_chart_version }}{% endif %}
      --namespace {{ helm_chart_namespace }}
      {% if helm_chart_create_namespace | default(false) %}--create-namespace{% endif %}
      --values {{ helm_render_dir.path }}/inline-values.yaml
      --output-dir {{ helm_render_dir.path }}
  register: helm_template_result
  changed_when: true
  when:
    - helm_chart_values is defined
    - helm_chart_values | length > 0
    - helm_chart_values_file is not defined

- name: "Apply rendered manifests to cluster"
  ansible.builtin.include_tasks: apply_manifests.yaml
  vars:
    manifests_dir: "{{ helm_render_dir.path }}"

- name: "Clean up temporary directory"
  delegate_to: localhost
  ansible.builtin.file:
    path: "{{ helm_render_dir.path }}"
    state: absent
```

#### 1.4: Implement Manifest Application Logic

**File**: `ansible/roles/helm_renderer/tasks/apply_manifests.yaml`

```yaml
---
# Apply rendered Kubernetes manifests to cluster

- name: "Create namespace if needed"
  kubernetes.core.k8s:
    api_version: v1
    kind: Namespace
    name: "{{ helm_chart_namespace }}"
    state: present
  when: helm_chart_create_namespace | default(false)

- name: "Find all rendered YAML files"
  delegate_to: localhost
  ansible.builtin.find:
    paths: "{{ manifests_dir }}"
    patterns: "*.yaml"
    recurse: true
  register: manifest_files

- name: "Apply each manifest file to cluster"
  kubernetes.core.k8s:
    state: present
    src: "{{ item.path }}"
    namespace: "{{ helm_chart_namespace }}"
  loop: "{{ manifest_files.files }}"
  loop_control:
    label: "{{ item.path | basename }}"

- name: "Wait for deployments to be ready"
  kubernetes.core.k8s_info:
    api_version: apps/v1
    kind: Deployment
    namespace: "{{ helm_chart_namespace }}"
  register: deployments_info
  until: >
    deployments_info.resources |
    selectattr('status.conditions', 'defined') |
    selectattr('status.conditions', 'search', 'type=="Available"') |
    selectattr('status.conditions', 'search', 'status=="True"') |
    list | length == (deployments_info.resources | length)
  retries: "{{ (helm_chart_timeout | default('5m') | regex_replace('m', '') | int) }}"
  delay: 10
  when:
    - helm_chart_wait | default(false)
    - deployments_info.resources | length > 0
```

#### 1.5: Implement Main Entry Point

**File**: `ansible/roles/helm_renderer/tasks/main.yaml`

```yaml
---
# Main entry point for helm_renderer role
# Decides between Helm-native deployment or local rendering based on use_staging_node flag

- name: "Deploy chart using Helm (non-air-gapped mode)"
  kubernetes.core.helm:
    name: "{{ helm_chart_name }}"
    chart_ref: "{{ helm_chart_ref }}"
    chart_version: "{{ helm_chart_version | default(omit) }}"
    release_namespace: "{{ helm_chart_namespace }}"
    create_namespace: "{{ helm_chart_create_namespace | default(true) }}"
    release_state: present
    update_repo_cache: "{{ (helm_repo_name is defined) | ternary(true, false) }}"
    wait: "{{ helm_chart_wait | default(true) }}"
    wait_timeout: "{{ helm_chart_timeout | default('5m') }}"
    atomic: true
    values: "{{ helm_chart_values | default(omit) }}"
    values_files: "{{ helm_chart_values_files | default(omit) }}"
  when: not (use_staging_node | default(false) | bool)

- name: "Deploy chart using local rendering (air-gapped mode)"
  ansible.builtin.include_tasks: render_chart.yaml
  when: use_staging_node | default(false) | bool
```

#### 1.6: Create Molecule Tests

**File**: `ansible/roles/helm_renderer/molecule/default/converge.yml`

```yaml
---
- name: Converge
  hosts: localhost
  gather_facts: false
  vars:
    use_staging_node: true
    kubeconfig_yaml: /tmp/test_kubeconfig

  tasks:
    - name: Test rendering a simple chart
      ansible.builtin.include_role:
        name: helm_renderer
      vars:
        helm_chart_name: nginx
        helm_chart_ref: oci://registry-1.docker.io/bitnamicharts/nginx
        helm_chart_namespace: test-nginx
        helm_chart_version: "18.2.4"
        helm_chart_values:
          service:
            type: ClusterIP
```

**File**: `ansible/roles/helm_renderer/molecule/default/verify.yml`

```yaml
---
- name: Verify
  hosts: localhost
  gather_facts: false

  tasks:
    - name: Verify helm_renderer role variables
      ansible.builtin.assert:
        that:
          - helm_chart_name is defined
          - helm_chart_ref is defined
          - helm_chart_namespace is defined
        fail_msg: "Required variables not defined"
```

### Phase 2: Refactor Public Chart Deployments

**Goal**: Update all playbooks using public Helm charts to use the new helm_renderer role

**Approach**: Modify each playbook to conditionally use helm_renderer role while maintaining backward compatibility

#### 2.1: orchestrator.yaml - cert-manager

**Current implementation** (lines 22-39):

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

**Refactored implementation**:

```yaml
- name: Deploy cert-manager
  when: not (use_staging_node | default(false) | bool)
  block:
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

- name: Deploy cert-manager (air-gapped)
  when: use_staging_node | default(false) | bool
  ansible.builtin.include_role:
    name: helm_renderer
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

**Optimization**: Create a wrapper task to reduce duplication

**File**: `ansible/playbooks/tasks/deploy_helm_chart.yaml`

```yaml
---
# Unified wrapper for Helm chart deployment
# Handles both air-gapped and non-air-gapped modes

- name: "Deploy {{ helm_chart_name }} using Helm (non-air-gapped)"
  when: not (use_staging_node | default(false) | bool)
  block:
    - name: "Add Helm repository for {{ helm_chart_name }}"
      kubernetes.core.helm_repository:
        name: "{{ helm_repo_name }}"
        repo_url: "{{ helm_repo_url }}"
      when:
        - helm_repo_name is defined
        - helm_repo_url is defined

    - name: "Install {{ helm_chart_name }}"
      kubernetes.core.helm:
        name: "{{ helm_chart_name }}"
        chart_ref: "{{ helm_chart_ref }}"
        chart_version: "{{ helm_chart_version | default(omit) }}"
        release_namespace: "{{ helm_chart_namespace }}"
        create_namespace: "{{ helm_chart_create_namespace | default(true) }}"
        release_state: present
        update_repo_cache: "{{ (helm_repo_name is defined) | ternary(true, false) }}"
        wait: "{{ helm_chart_wait | default(true) }}"
        wait_timeout: "{{ helm_chart_timeout | default('5m') }}"
        atomic: true
        values: "{{ helm_chart_values | default(omit) }}"
        values_files: "{{ helm_chart_values_files | default(omit) }}"

- name: "Deploy {{ helm_chart_name }} (air-gapped)"
  when: use_staging_node | default(false) | bool
  ansible.builtin.include_role:
    name: helm_renderer
```

**Simplified refactored implementation using wrapper**:

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

#### 2.2: orchestrator.yaml - cass-operator

**Current implementation** (lines 41-58):

```yaml
- name: Add k8ssandra Helm repository
  kubernetes.core.helm_repository:
    name: k8ssandra
    repo_url: https://helm.k8ssandra.io/stable

- name: Install the cass-operator
  kubernetes.core.helm:
    name: cass-operator
    chart_ref: k8ssandra/cass-operator
    chart_version: ~0.55.2
    release_namespace: '{{ temporal_namespace }}'
    create_namespace: true
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: 10m
    atomic: true
```

**Refactored implementation**:

```yaml
- name: Deploy cass-operator
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: cass-operator
    helm_chart_ref: k8ssandra/cass-operator
    helm_chart_version: "~0.55.2"
    helm_chart_namespace: "{{ temporal_namespace }}"
    helm_repo_name: k8ssandra
    helm_repo_url: https://helm.k8ssandra.io/stable
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 10m
```

#### 2.3: orchestrator.yaml - Temporal

**Current implementation** (lines 93-157):

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
      prometheus:
        enabled: false
      grafana:
        enabled: false
      # ... (extensive values configuration)
```

**Refactored implementation**:

```yaml
- name: Set Temporal chart values
  ansible.builtin.set_fact:
    temporal_chart_values:
      prometheus:
        enabled: false
      grafana:
        enabled: false
      server:
        replicaCount: 1
        config:
          namespaces:
            create: true
          persistence:
            default:
              driver: 'cassandra'
              cassandra:
                hosts: ['temporal-cassandra-dc1-service.{{ temporal_namespace }}']
                port: 9042
                keyspace: temporal
                existingSecret: 'temporal-cassandra-superuser'
                replicationFactor: 1
                consistency:
                  default:
                    consistency: 'local_quorum'
                    serialConsistency: 'local_serial'
      cassandra:
        enabled: false
      elasticsearch:
        replicas: 1
        persistence:
          enabled: true
        volumeClaimTemplate:
          accessModes:
            - ReadWriteOnce
          storageClassName: '{{ elasticsearch_storage_class }}'
          resources:
            requests:
              storage: '{{ elasticsearch_storage_size | default("100Gi") }}'
      web:
        additionalEnv:
          - name: TEMPORAL_UI_PUBLIC_PATH
            value: /temporal
        ingress:
          enabled: true
          ingressClassName: traefik
          hosts:
            - '{{ server_hostname }}/temporal'

- name: Deploy Temporal
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: temporal
    helm_chart_ref: temporal/temporal
    helm_chart_version: "~0.62.0"
    helm_chart_namespace: "{{ temporal_namespace }}"
    helm_repo_name: temporal
    helm_repo_url: https://raw.githubusercontent.com/temporalio/helm-charts/refs/heads/gh-pages/
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 15m
    helm_chart_values: "{{ temporal_chart_values }}"
  register: temporal_helm_result
```

**Note**: The `register: temporal_helm_result` is used later in the playbook to wait for the schema job. In air-gapped mode, this won't capture the Helm revision, so we need to adjust the schema job wait logic.

**Schema job wait adjustment** (lines 158-164):

**Current**:
```yaml
- name: Wait for Temporal schema job to complete
  shell: >-
    if kubectl -n {{ temporal_namespace }} get jobs temporal-schema-{{ temporal_helm_result.status.revision }} >/dev/null 2>&1; then
      kubectl -n {{ temporal_namespace }} wait --for=condition=complete --timeout=300s job/temporal-schema-{{ temporal_helm_result.status.revision }}
    fi
  register: temporal_schema
  changed_when: false
```

**Refactored** (works in both modes):
```yaml
- name: Wait for Temporal schema job to complete
  shell: >-
    JOB_NAME=$(kubectl -n {{ temporal_namespace }} get jobs -l app.kubernetes.io/component=schema -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "");
    if [ -n "$JOB_NAME" ]; then
      kubectl -n {{ temporal_namespace }} wait --for=condition=complete --timeout=300s job/$JOB_NAME;
    fi
  register: temporal_schema
  changed_when: false
```

#### 2.4: jupyter.yaml - JupyterHub

**Current implementation** (lines 46-84):

```yaml
- name: Add JupyterHub Helm repository
  kubernetes.core.helm_repository:
    name: jupyterhub
    repo_url: https://raw.githubusercontent.com/jupyterhub/helm-chart/refs/heads/gh-pages/

# ... (ConfigMap setup and values merging)

- name: Install JupyterHub using Helm
  kubernetes.core.helm:
    state: present
    name: jupyter
    chart_ref: jupyterhub/jupyterhub
    release_namespace: jupyter
    create_namespace: true
    update_repo_cache: true
    chart_version: ~4.2.0
    wait: true
    wait_timeout: 10m
    atomic: true
    values: '{{ jupyter_values }}'
```

**Refactored implementation**:

```yaml
# ... (ConfigMap setup and values merging remain unchanged)

- name: Deploy JupyterHub
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: jupyter
    helm_chart_ref: jupyterhub/jupyterhub
    helm_chart_version: "~4.2.0"
    helm_chart_namespace: jupyter
    helm_repo_name: jupyterhub
    helm_repo_url: https://raw.githubusercontent.com/jupyterhub/helm-chart/refs/heads/gh-pages/
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 10m
    helm_chart_values: "{{ jupyter_values }}"
```

#### 2.5: services/superset.yaml

**Current implementation** (lines 1-70):

```yaml
- name: Add Superset Helm repository
  kubernetes.core.helm_repository:
    name: superset
    repo_url: https://apache.github.io/superset

# ... (ConfigMap creation for dashboards)

- name: Install/Upgrade Superset Helm chart
  kubernetes.core.helm:
    name: superset
    chart_ref: superset/superset
    chart_version: 0.14.2
    release_namespace: superset
    create_namespace: true
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: 15m
    atomic: true
    values:
      # ... (extensive values)
```

**Refactored implementation**:

```yaml
# ... (ConfigMap creation remains unchanged)

- name: Set Superset chart values
  ansible.builtin.set_fact:
    superset_chart_values:
      image:
        repository: '{{ superset_image | default("ghcr.io/washu-tag/superset") }}'
        tag: 4.1.2
      redis:
        image:
          repository: bitnamilegacy/redis
      extraSecretEnv:
        SUPERSET_SECRET_KEY: '{{ superset_secret }}'
      ingress:
        enabled: true
        ingressClassName: traefik
        path: /
        hosts:
          - '{{ server_hostname }}'
      postgresql:
        enabled: false
      supersetNode:
        connections:
          db_host: 'postgresql-cluster-rw.{{ postgres_cluster_namespace }}'
          db_port: '5432'
          db_user: '{{ superset_postgres_user }}'
          db_pass: '{{ superset_postgres_password }}'
          db_name: superset
      configOverrides:
        branding: |
          APP_NAME = "Scout Analytics"
          APP_ICON = "https://{{ server_hostname }}/launchpad/scout.png"
          APP_ICON_WIDTH = 200
          LOGO_TARGET_PATH = "https://{{ server_hostname }}/launchpad"
          LOGO_TOOLTIP = "Scout Launchpad"
          FAVICONS = [{"href": "https://{{ server_hostname }}/launchpad/scout.png"}]
        custom_routing: |
          from flask import redirect, url_for
          from flask_appbuilder import expose, IndexView

          from superset.superset_typing import FlaskResponse

          class SupersetIndexView(IndexView):
              @expose("/")
              def index(self) -> FlaskResponse:
                  return redirect(url_for("Superset.dashboard", dashboard_id_or_slug="scout"))

          FAB_INDEX_VIEW = f"{SupersetIndexView.__module__}.{SupersetIndexView.__name__}"
        sql_alchemy: |
          SQLALCHEMY_ENGINE_OPTIONS = {
              "pool_size": 20,
              "max_overflow": 30,
              "pool_timeout": 60,
          }
      extraVolumes:
        - name: dashboard-config
          configMap:
            name: dashboard-config
            items: '{{ superset_dashboard_items | default([]) }}'
      extraVolumeMounts:
        - name: dashboard-config
          mountPath: /app/dashboard-config
      init:
        initscript: |-
          {% raw %}
          #!/bin/sh
          set -eu
          echo "Upgrading DB schema..."
          superset db upgrade
          echo "Initializing roles..."
          superset init
          {{ if .Values.init.createAdmin }}
          echo "Creating admin user..."
          superset fab create-admin \
                          --username {{ .Values.init.adminUser.username }} \
                          --firstname {{ .Values.init.adminUser.firstname }} \
                          --lastname {{ .Values.init.adminUser.lastname }} \
                          --email {{ .Values.init.adminUser.email }} \
                          --password {{ .Values.init.adminUser.password }} \
                          || true
          {{- end }}
          {{ if .Values.init.loadExamples }}
          echo "Loading examples..."
          superset load_examples
          {{- end }}
          if [ -f "{{ .Values.extraConfigMountPath }}/import_datasources.yaml" ]; then
            echo "Importing database connections.... "
            superset import_datasources -p {{ .Values.extraConfigMountPath }}/import_datasources.yaml
          fi
          # Import the dashboard
          if [ -d /app/dashboard-config ]; then
            echo "Importing dashboard..."
            cd /app/dashboard-config
            zip -r ~/dashboard.zip .
            cd -
            superset import-dashboards -p ~/dashboard.zip -u admin
            result=$?
            if [ $result != 0 ]; then
              echo "Error importing dashboard"
              exit $result
            fi
            rm -f ~/dashboard.zip
            echo "Dashboard imported successfully"
          fi
          {% endraw %}

- name: Deploy Superset
  ansible.builtin.include_tasks: ../tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: superset
    helm_chart_ref: superset/superset
    helm_chart_version: "0.14.2"
    helm_chart_namespace: superset
    helm_repo_name: superset
    helm_repo_url: https://apache.github.io/superset
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 15m
    helm_chart_values: "{{ superset_chart_values }}"
```

#### 2.6: services/trino.yaml

**Current implementation**:

```yaml
- name: Add Trino Helm repository
  kubernetes.core.helm_repository:
    name: trino
    repo_url: https://trinodb.github.io/charts

- name: Install/Upgrade Trino Helm chart
  kubernetes.core.helm:
    name: trino
    chart_ref: trino/trino
    chart_version: ~1.38.0
    release_namespace: trino
    create_namespace: true
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: 5m
    atomic: true
    values:
      catalogs:
        delta: |
          connector.name=delta_lake
          hive.metastore.uri={{ hive_metastore_endpoint }}
          # ... (catalog configuration)
```

**Refactored implementation**:

```yaml
- name: Deploy Trino
  ansible.builtin.include_tasks: ../tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: trino
    helm_chart_ref: trino/trino
    helm_chart_version: "~1.38.0"
    helm_chart_namespace: trino
    helm_repo_name: trino
    helm_repo_url: https://trinodb.github.io/charts
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 5m
    helm_chart_values:
      catalogs:
        delta: |
          connector.name=delta_lake
          hive.metastore.uri={{ hive_metastore_endpoint }}
          delta.security=ALLOW_ALL
          delta.enable-non-concurrent-writes=true
          fs.native-s3.enabled=true
          s3.aws-access-key={{ s3_lake_writer }}
          s3.aws-secret-key={{ s3_lake_writer_secret }}
          s3.region={{ s3_region }}
          s3.endpoint={{ s3_endpoint }}
          s3.path-style-access=true
      coordinator:
        annotations:
          prometheus.io/trino_scrape: 'true'
      worker:
        annotations:
          prometheus.io/trino_scrape: 'true'
```

#### 2.7: services/minio.yaml - MinIO Operator and Tenant

**Current implementation** (lines 4-21, 90-134):

```yaml
- name: Add Minio Helm repository
  kubernetes.core.helm_repository:
    name: minio-operator
    repo_url: https://operator.min.io

- name: Install/Upgrade Minio Operator
  kubernetes.core.helm:
    name: minio-operator
    chart_ref: minio-operator/operator
    chart_version: "{{ minio_version | default('~7.1.0') }}"
    release_namespace: minio-operator
    create_namespace: true
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: "{{ minio_operator_wait_timeout | default('1m') }}"
    atomic: true

# ... (Middleware and secret creation)

- name: Install/Upgrade Minio Tenant
  kubernetes.core.helm:
    name: '{{ minio_tenant_namespace }}'
    chart_ref: minio-operator/tenant
    chart_version: "{{ minio_version | default('~7.1.0') }}"
    release_namespace: '{{ minio_tenant_namespace }}'
    release_state: present
    update_repo_cache: true
    wait: true
    wait_timeout: "{{ minio_tenant_wait_timeout | default('5m') }}"
    atomic: true
    values:
      # ... (extensive tenant configuration)
```

**Refactored implementation**:

```yaml
- name: Deploy MinIO Operator
  ansible.builtin.include_tasks: ../tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: minio-operator
    helm_chart_ref: minio-operator/operator
    helm_chart_version: "{{ minio_version | default('~7.1.0') }}"
    helm_chart_namespace: minio-operator
    helm_repo_name: minio-operator
    helm_repo_url: https://operator.min.io
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: "{{ minio_operator_wait_timeout | default('1m') }}"

# ... (Middleware and secret creation remain unchanged)

- name: Set MinIO Tenant chart values
  ansible.builtin.set_fact:
    minio_tenant_chart_values:
      tenant:
        name: '{{ minio_tenant_name }}'
        configSecret:
          name: "{{ minio_env_secret_name | default('minio-env-configuration') }}"
          existingSecret: true
        env: '{{ minio_env_variables }}'
        pools:
          - servers: '{{ groups["minio_hosts"] | length | default(1) }}'
            name: pool-0
            volumesPerServer: '{{ minio_volumes_per_server | default(1) }}'
            size: "{{ minio_storage_size | default('100Gi') }}"
            storageClassName: "{{ minio_storage_class | default('minio-storage') }}"
            tolerations:
              - key: 'node-role.kubernetes.io/control-plane'
                operator: Exists
                effect: PreferNoSchedule
            affinity:
              podAntiAffinity:
                requiredDuringSchedulingIgnoredDuringExecution:
                  - labelSelector:
                      matchLabels:
                        v1.min.io/tenant: scout
                    topologyKey: kubernetes.io/hostname
        metrics:
          enabled: true
          port: 9000
          protocol: http
        certificate:
          requestAutoCert: false
        buckets: '{{ minio_buckets }}'
        users: '{{ minio_credentials }}'
      ingress: '{{ minio_ingress_config }}'

- name: Deploy MinIO Tenant
  ansible.builtin.include_tasks: ../tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: "{{ minio_tenant_namespace }}"
    helm_chart_ref: minio-operator/tenant
    helm_chart_version: "{{ minio_version | default('~7.1.0') }}"
    helm_chart_namespace: "{{ minio_tenant_namespace }}"
    helm_repo_name: minio-operator
    helm_repo_url: https://operator.min.io
    helm_chart_create_namespace: false
    helm_chart_wait: true
    helm_chart_timeout: "{{ minio_tenant_wait_timeout | default('5m') }}"
    helm_chart_values: "{{ minio_tenant_chart_values }}"
```

#### 2.8: services/grafana.yaml and services/prometheus.yaml

**Note**: These files need to be created/examined. Based on the inventory, these are referenced from `monitor.yaml` playbook which we'll need to inspect.

**Action item**: Check if `monitor.yaml` exists and contains Grafana/Prometheus deployments. If not, these may be future additions.

### Phase 3: Refactor Local Chart Deployments

**Goal**: Update playbooks deploying local Scout charts to use the helm_renderer role

**Key difference**: Local charts don't need Helm repository, just local path

#### 3.1: explorer.yaml

**Current implementation** (lines 23-50):

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
      ingress:
        enabled: true
        className: traefik
        annotations:
          traefik.ingress.kubernetes.io/router.middlewares: >
            kube-system-explorer-strip-prefix@kubernetescrd
        hosts:
          - host: '{{ server_hostname }}'
            paths:
              - path: /launchpad
                pathType: Prefix
    kubeconfig: '{{ local_kubeconfig_yaml }}'
  delegate_to: localhost
```

**Refactored implementation**:

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
      ingress:
        enabled: true
        className: traefik
        annotations:
          traefik.ingress.kubernetes.io/router.middlewares: >
            kube-system-explorer-strip-prefix@kubernetescrd
        hosts:
          - host: '{{ server_hostname }}'
            paths:
              - path: /launchpad
                pathType: Prefix
```

**Note**: Local charts already have `delegate_to: localhost` in current implementation. The wrapper task will handle delegation appropriately.

#### 3.2: extractor.yaml - hl7log-extractor and hl7-transformer

**Current implementation** (lines 74-94):

```yaml
- name: Deploy HL7Log Extractor
  kubernetes.core.helm:
    name: hl7log-extractor
    chart_ref: '{{ scout_repo_dir }}/helm/extractor/hl7log-extractor'
    release_namespace: '{{ extractor_namespace }}'
    release_state: present
    wait: true
    wait_timeout: 5m
    atomic: true
    values: "{{ lookup('template', 'templates/hl7log-extractor.values.yaml.j2') | from_yaml }}"

- name: Deploy HL7 Transformer
  kubernetes.core.helm:
    name: hl7-transformer
    chart_ref: '{{ scout_repo_dir }}/helm/extractor/hl7-transformer'
    release_namespace: '{{ extractor_namespace }}'
    release_state: present
    wait: true
    wait_timeout: 5m
    atomic: true
    values: "{{ lookup('template', 'templates/hl7-transformer.values.yaml.j2') | from_yaml }}"
```

**Refactored implementation**:

```yaml
- name: Load HL7Log Extractor values
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
    helm_chart_values: "{{ hl7log_extractor_values }}"

- name: Load HL7 Transformer values
  ansible.builtin.set_fact:
    hl7_transformer_values: "{{ lookup('template', 'templates/hl7-transformer.values.yaml.j2') | from_yaml }}"

- name: Deploy HL7 Transformer
  ansible.builtin.include_tasks: tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: hl7-transformer
    helm_chart_ref: '{{ scout_repo_dir }}/helm/extractor/hl7-transformer'
    helm_chart_namespace: '{{ extractor_namespace }}'
    helm_chart_create_namespace: false
    helm_chart_wait: true
    helm_chart_timeout: 5m
    helm_chart_values: "{{ hl7_transformer_values }}"
```

#### 3.3: services/hive.yaml

**Current implementation**:

```yaml
- name: Load values file
  ansible.builtin.include_vars:
    file: vars/hive.values.yaml
    name: hive_values

- name: Install Hive Metastore Service (HMS) with helm
  kubernetes.core.helm:
    name: hive-metastore
    chart_ref: '{{ scout_repo_dir }}/helm/lake/hive-metastore'
    release_namespace: '{{ hive_namespace }}'
    create_namespace: true
    release_state: present
    wait: true
    wait_timeout: 5m
    atomic: true
    values: '{{ hive_values }}'
    kubeconfig: '{{ local_kubeconfig_yaml }}'
  delegate_to: localhost
```

**Refactored implementation**:

```yaml
- name: Load values file
  ansible.builtin.include_vars:
    file: vars/hive.values.yaml
    name: hive_values

- name: Deploy Hive Metastore Service
  ansible.builtin.include_tasks: ../tasks/deploy_helm_chart.yaml
  vars:
    helm_chart_name: hive-metastore
    helm_chart_ref: '{{ scout_repo_dir }}/helm/lake/hive-metastore'
    helm_chart_namespace: '{{ hive_namespace }}'
    helm_chart_create_namespace: true
    helm_chart_wait: true
    helm_chart_timeout: 5m
    helm_chart_values: "{{ hive_values }}"
```

#### 3.4: dcm4chee.yaml and orthanc.yaml

**Action item**: These playbooks need to be inspected to see current Helm deployment patterns. Based on the inventory, they should have local chart deployments similar to explorer.

**Expected pattern**:
- Current: Direct `kubernetes.core.helm` with local chart path
- Refactored: Use `deploy_helm_chart.yaml` wrapper task

### Phase 4: Testing and Validation

**Goal**: Ensure all refactored deployments work in both air-gapped and non-air-gapped modes

#### 4.1: Unit Testing (Molecule)

**Test scope**:
- `helm_renderer` role with sample charts
- Rendering logic for public repos
- Rendering logic for local paths
- Error handling (missing Helm, bad chart reference, etc.)

**Test commands**:
```bash
cd ansible/roles/helm_renderer
uvx molecule test -s default
```

#### 4.2: Integration Testing - Non-Air-Gapped Mode

**Goal**: Verify no regressions in existing deployments

**Test environment**: Existing CI/CD pipeline or local k3s cluster

**Test procedure**:
1. Deploy with `use_staging_node: false` (default)
2. Verify all services deploy successfully
3. Verify services pass health checks
4. Compare with baseline deployment (pre-refactoring)

**Test commands**:
```bash
cd ansible
ansible-playbook -i inventory.test.yaml playbooks/main.yaml
```

**Success criteria**:
- All playbooks complete without errors
- All pods reach Running state
- Health check endpoints return 200 OK
- Deployment time comparable to baseline (<10% regression)

#### 4.3: Integration Testing - Air-Gapped Mode

**Goal**: Verify air-gapped deployments work end-to-end

**Test environment**: cluster03 with staging node (Harbor deployed)

**Prerequisites**:
1. Harbor deployed on staging node (Phase 5 from staging-node-implementation-plan.md)
2. Registry mirrors configured on k3s cluster nodes
3. Control node (localhost) has Helm installed

**Test procedure**:
1. Deploy with `use_staging_node: true`
2. Verify charts render on localhost
3. Verify manifests apply to cluster
4. Verify services deploy successfully
5. Verify container images pulled through Harbor
6. Verify services pass health checks

**Test commands**:
```bash
cd ansible

# Deploy staging infrastructure first
ansible-playbook -i inventory.cluster03-roles-staging.yaml playbooks/staging-k3s.yaml
ansible-playbook -i inventory.cluster03-roles-staging.yaml playbooks/harbor.yaml

# Deploy Scout services in air-gapped mode
ansible-playbook -i inventory.cluster03-roles-staging.yaml playbooks/main.yaml
```

**Success criteria**:
- All charts render successfully on localhost
- All manifests apply without errors
- All pods reach Running state
- Container images pulled through Harbor (verify with `kubectl describe pod`)
- Health check endpoints return 200 OK
- No internet access required from k3s cluster nodes

#### 4.4: Critical Path Testing

**Focus areas** (services with complex deployment logic):

##### Temporal Deployment
- Schema migration job completes
- Cassandra datacenter ready
- Elasticsearch cluster healthy
- Web UI accessible

**Test**:
```bash
# Wait for Temporal schema job
kubectl -n temporal get jobs -l app.kubernetes.io/component=schema

# Verify Temporal workflows
kubectl exec -n temporal service/temporal-admintools -- temporal workflow list
```

##### Superset Deployment
- Dashboard ConfigMap mounted
- Init script runs successfully
- Dashboard import completes
- Superset UI accessible with Scout branding

**Test**:
```bash
# Check Superset init logs
kubectl -n superset logs -l app=superset,component=init

# Verify dashboard import
kubectl -n superset logs -l app=superset | grep "Dashboard imported successfully"
```

##### MinIO Deployment
- Operator and Tenant both deploy
- IAM bootstrap job completes
- Policies attached to users
- MinIO console accessible

**Test**:
```bash
# Check MinIO tenant status
kubectl -n minio-tenant get tenant scout -o jsonpath='{.status.currentState}'

# Verify IAM bootstrap
kubectl -n minio-tenant logs job/bootstrap-minio-iam
```

#### 4.5: Rollback Testing

**Goal**: Verify we can roll back to Helm-native deployment if air-gapped mode fails

**Test procedure**:
1. Deploy in air-gapped mode (`use_staging_node: true`)
2. Identify a failure scenario (e.g., chart rendering error)
3. Switch to non-air-gapped mode (`use_staging_node: false`)
4. Re-deploy and verify recovery

**Success criteria**:
- Switching deployment modes doesn't require manual cleanup
- Services redeploy successfully in non-air-gapped mode
- No residual state from air-gapped deployment

#### 4.6: Performance Testing

**Goal**: Measure deployment time impact of local rendering

**Metrics to collect**:
- Total playbook execution time
- Time per service deployment
- Localhost rendering time vs Helm install time

**Baseline** (non-air-gapped, Helm-native):
```bash
time ansible-playbook -i inventory.test.yaml playbooks/main.yaml
```

**Air-gapped** (local rendering):
```bash
time ansible-playbook -i inventory.cluster03-roles-staging.yaml playbooks/main.yaml
```

**Acceptable thresholds**:
- Total deployment time: +15% max (rendering overhead expected)
- Individual service deployment: +20% max
- If exceeded, investigate optimization opportunities

## Backward Compatibility

### Inventory Configuration

**No changes required** for existing inventories. The `use_staging_node` flag defaults to `false`, maintaining current behavior.

**Air-gapped inventories** add the flag:

```yaml
k3s_cluster:
  vars:
    use_staging_node: true
```

### Playbook Execution

**Non-air-gapped deployments** (current behavior):
```bash
ansible-playbook -i inventory.yaml playbooks/main.yaml
# Uses kubernetes.core.helm module directly
```

**Air-gapped deployments** (new behavior):
```bash
ansible-playbook -i inventory.airgapped.yaml playbooks/main.yaml
# Uses local chart rendering + k8s apply
```

**No command-line changes required** - behavior controlled by inventory variable.

### Migration Path

Organizations can adopt air-gapped deployments incrementally:

1. **Phase 1**: Deploy Harbor on staging node (already complete)
2. **Phase 2**: Test one service (e.g., explorer) in air-gapped mode
3. **Phase 3**: Enable air-gapped mode for all services
4. **Phase 4**: Remove internet access from k3s cluster nodes (infrastructure team)

**Rollback**: Change `use_staging_node: false` and redeploy. No manual cleanup required.

## Success Criteria

### Functional Requirements

- [ ] All 16 Helm charts deploy successfully in air-gapped mode
- [ ] Non-air-gapped deployments continue working without regression
- [ ] Temporal schema migration job completes in both modes
- [ ] Superset dashboard import works in both modes
- [ ] MinIO IAM bootstrap completes in both modes
- [ ] All services pass health checks in both modes

### Performance Requirements

- [ ] Air-gapped deployment time within 15% of baseline
- [ ] Chart rendering completes within 2 minutes per chart
- [ ] No noticeable impact on non-air-gapped deployment time

### Quality Requirements

- [ ] Molecule tests pass for helm_renderer role
- [ ] CI/CD tests both deployment modes (air-gapped + non-air-gapped)
- [ ] Ansible lint passes for all modified playbooks
- [ ] Pre-commit hooks pass for all changes

### Documentation Requirements

- [ ] Architecture decision documented (this document's companion)
- [ ] Implementation plan complete (this document)
- [ ] Playbook changes documented with inline comments
- [ ] Troubleshooting guide created for air-gapped deployments
- [ ] Example inventory files updated

## Rollout Plan

### Development Phase (Week 1-2)

**Week 1: Infrastructure**
- [ ] Day 1-2: Create helm_renderer role structure
- [ ] Day 3-4: Implement rendering logic (render_chart.yaml)
- [ ] Day 5: Implement application logic (apply_manifests.yaml)
- [ ] Day 5: Create deploy_helm_chart.yaml wrapper task

**Week 2: Playbook Refactoring**
- [ ] Day 1-2: Refactor orchestrator.yaml (cert-manager, cass-operator, Temporal)
- [ ] Day 3: Refactor jupyter.yaml (JupyterHub)
- [ ] Day 4: Refactor analytics.yaml (Superset, Trino)
- [ ] Day 5: Refactor lake.yaml (MinIO, Hive)

### Testing Phase (Week 3)

**Week 3: Validation**
- [ ] Day 1: Refactor remaining playbooks (explorer, extractor, dcm4chee, orthanc)
- [ ] Day 2-3: Integration testing - non-air-gapped mode (regression test)
- [ ] Day 4-5: Integration testing - air-gapped mode (cluster03)

### Production Rollout (Week 4)

**Week 4: Deployment**
- [ ] Day 1: Code review and approval
- [ ] Day 2: Merge to main branch
- [ ] Day 3: Deploy to staging environment (cluster03)
- [ ] Day 4: Monitor staging environment (24h observation)
- [ ] Day 5: Document lessons learned, prepare for production

**Post-rollout**:
- Production deployments use new air-gapped capability as needed
- Non-air-gapped deployments continue using existing approach
- Monitor deployment metrics for 2 weeks

## Risk Mitigation

### Risk 1: Helm Rendering Differences

**Risk**: `helm template` output may differ from `helm install` in edge cases

**Likelihood**: Low
**Impact**: Medium

**Mitigation**:
- Extensive testing with all Scout charts
- Compare rendered YAML between `helm template` and `helm get manifest`
- Document any known differences in troubleshooting guide

### Risk 2: Missing Helm Hooks

**Risk**: Services relying on Helm hooks may not deploy correctly

**Likelihood**: Low (hooks analyzed, none critical)
**Impact**: Medium

**Mitigation**:
- Hook analysis completed (Temporal hooks are test-only)
- Manual workarounds already in place (schema job wait)
- Add retry logic for services with ordering dependencies

### Risk 3: Performance Degradation

**Risk**: Local rendering adds overhead to deployment time

**Likelihood**: Medium
**Impact**: Low

**Mitigation**:
- Benchmark before/after refactoring
- Optimize rendering (parallel chart rendering if needed)
- Accept reasonable overhead (15%) for air-gapped capability

### Risk 4: Localhost Dependencies

**Risk**: Control node must have Helm installed and configured

**Likelihood**: Low
**Impact**: Low

**Mitigation**:
- Document Helm installation requirements
- Add validation task to check Helm availability
- Provide clear error messages if Helm missing

### Risk 5: Compatibility Issues with Future Helm Versions

**Risk**: Helm template output format may change in future releases

**Likelihood**: Low
**Impact**: Low

**Mitigation**:
- Pin Helm version requirement in documentation
- Test with multiple Helm versions (3.12+)
- Monitor Helm release notes for breaking changes

## Maintenance and Operations

### Ongoing Responsibilities

**Weekly**:
- Monitor deployment success rates (air-gapped vs non-air-gapped)
- Review Ansible logs for rendering errors

**Monthly**:
- Review Helm version compatibility
- Update public chart versions as needed
- Performance benchmarking

**Quarterly**:
- Disaster recovery testing (staging node failure scenarios)
- Review and update documentation

### Troubleshooting Guide

**Common issues and solutions**:

1. **Chart rendering fails on localhost**
   - Check: `helm version` on localhost
   - Check: Helm repository accessibility
   - Check: Chart version exists

2. **Manifest application fails**
   - Check: Namespace exists
   - Check: RBAC permissions
   - Check: Resource conflicts (existing resources with same name)

3. **Service not starting after deployment**
   - Check: Container images pulled through Harbor
   - Check: ConfigMaps and Secrets created
   - Check: PersistentVolumes available

4. **Performance issues**
   - Check: Localhost CPU/memory during rendering
   - Check: Network latency to cluster
   - Consider: Parallel chart rendering

### Monitoring and Alerting

**Metrics to track**:
- Deployment success rate by mode (air-gapped vs non-air-gapped)
- Average deployment time per service
- Chart rendering failures
- Manifest application failures

**Alerts to configure**:
- Air-gapped deployment failures (critical)
- Rendering time exceeds 5 minutes (warning)
- Helm version mismatch (warning)

## References

### Related Documents

- `air-gapped-helm-architecture-decision.md` - Architecture rationale
- `staging-node-implementation-plan.md` - Harbor container registry (Phases 1-5)
- `ansible_roles.md` - Ansible role documentation

### External Documentation

- [Helm Template Command](https://helm.sh/docs/helm/helm_template/)
- [Air-Gapped Helm Deployments](https://helm.sh/docs/topics/advanced/#helm-in-air-gapped-environments)
- [Kubernetes Apply Documentation](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_apply/)

### Tools and Dependencies

- Helm 3.12+ (on Ansible control node)
- Ansible 2.14+
- kubernetes.core collection 2.4.0+
- kubectl 1.28+ (on cluster nodes)

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2025-10-08 | Initial implementation plan | Claude Code |

---

**Document Status**: Proposed
**Next Steps**: Review, approve, and begin Phase 1 implementation
