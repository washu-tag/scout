# Scout Ansible-to-Helm Refactoring: Research and Recommendations

**Date:** November 2025
**Author:** Research conducted for Scout platform architecture team
**Purpose:** Evaluate the feasibility and approach for refactoring Scout's Ansible/Helm deployment to improve portability between on-premise and cloud environments

---

## Executive Summary

Scout currently uses Ansible to deploy a complex microservices platform consisting of ~20 services across multiple namespaces. The deployment is tightly coupled to on-premise infrastructure (K3s, local storage, specific hardware). This document analyzes the feasibility of refactoring to a more portable architecture that separates infrastructure provisioning from application deployment.

**Key Findings:**
- ✅ **Feasible**: The refactoring is highly feasible with a phased approach
- ✅ **Recommended Pattern**: Umbrella chart + Helmfile/ArgoCD for orchestration
- ⚠️ **Breaking Changes**: Storage provisioning, namespace structure, and deployment sequencing will require redesign
- 🎯 **Expected Outcome**: Single umbrella chart deployable to any Kubernetes cluster (EKS, GKE, AKS, K3s) with environment-specific values files

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Proposed Architecture](#2-proposed-architecture)
3. [Breaking Changes and Migration Path](#3-breaking-changes-and-migration-path)
4. [Dependency Ordering Solutions](#4-dependency-ordering-solutions)
5. [Storage Portability Strategy](#5-storage-portability-strategy)
6. [Namespace Strategy](#6-namespace-strategy)
7. [Orchestration Tool Comparison](#7-orchestration-tool-comparison)
8. [Implementation Roadmap](#8-implementation-roadmap)
9. [Specific Questions Answered](#9-specific-questions-answered)

---

## 1. Current State Analysis

### 1.1 What Ansible Does Today

The current Ansible deployment performs two distinct functions:

#### **Infrastructure Layer (Host-Specific)**
1. **K3s Installation** - Installs Kubernetes on bare metal/VMs
2. **Directory Provisioning** - Creates local filesystem paths for PersistentVolumes
3. **System Packages** - Installs python3-kubernetes, Helm CLI
4. **Traefik Configuration** - Reads TLS certs from filesystem, creates K8s secrets
5. **Registry Mirrors** - Configures Harbor pull-through proxy for air-gapped deployments
6. **Binary Installation** - Places k3s and helm binaries in /usr/local/bin

#### **Application Layer (Potentially Portable)**
1. **Helm Chart Deployment** - Deploys 20+ services via `helm install`
2. **Kubernetes Resources** - Creates StorageClasses, PVs, Secrets, ConfigMaps
3. **Service Configuration** - Generates values.yaml files from Ansible templates
4. **Sequencing** - Ensures services deploy in dependency order (PostgreSQL → Keycloak → apps)

### 1.2 Current Deployment Topology

```
main.yaml (orchestration playbook)
├── Infrastructure Plays
│   ├── k3s.yaml (K3s cluster installation)
│   ├── traefik.yaml (Ingress controller config)
│   ├── helm.yaml (Helm CLI installation)
│   └── gpu.yaml (GPU operator - optional)
│
└── Application Plays (Sequential)
    ├── postgres.yaml (CNPG operator + PostgreSQL cluster)
    ├── auth.yaml (Keycloak + OAuth2-Proxy)
    ├── lake.yaml (MinIO + Hive Metastore)
    ├── analytics.yaml (Trino + Superset)
    ├── orchestrator.yaml (Cassandra + Elasticsearch + Temporal)
    ├── extractor.yaml (hl7log-extractor + hl7-transformer)
    ├── jupyter.yaml (JupyterHub)
    ├── monitor.yaml (Prometheus + Loki + Grafana)
    └── launchpad.yaml (Landing page)
```

**Problem**: Application deployment is interleaved with infrastructure provisioning, making it impossible to deploy Scout on a pre-existing Kubernetes cluster without extensive Ansible modifications.

### 1.3 Current Chart Structure

**In-Repo Charts** (can migrate to umbrella):
- `helm/launchpad` - Landing page
- `helm/extractor/hl7log-extractor` - HL7 extraction service
- `helm/extractor/hl7-transformer` - HL7 transformation service
- `helm/hive-metastore` - Hive Metastore (deployed twice: write + readonly)
- `helm/dcm4chee` - DICOM PACS (optional)
- `helm/orthanc` - Lightweight PACS (optional)

**External Charts** (deployed via Ansible):
- PostgreSQL (CNPG operator from cnpg/cloudnative-pg)
- MinIO (minio-operator/operator + minio-operator/tenant)
- Cassandra (k8ssandra/cass-operator + cert-manager)
- Elasticsearch (elastic/eck-operator)
- Temporal (temporal/temporal)
- Trino (trino/trino)
- Superset (superset/superset) - with custom image
- JupyterHub (jupyterhub/jupyterhub) - with custom image
- Prometheus (prometheus-community/kube-prometheus-stack)
- Loki (grafana/loki)
- Grafana (grafana/grafana)
- Keycloak (raw YAML manifests, not Helm)

---

## 2. Proposed Architecture

### 2.1 Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Infrastructure Provisioning (Platform-Specific)    │
│                                                              │
│ On-Premise:           AWS:                  Azure:          │
│ • Ansible (K3s)       • Terraform (EKS)     • Terraform     │
│ • Local directories   • VPC, IAM, EBS CSI   • AKS, Storage  │
│ • TLS certs          • S3, RDS              • Blob, Azure DB│
│                                                              │
│ Outputs: kubeconfig, storage class names, DB endpoints      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Scout Umbrella Chart (Portable)                    │
│                                                              │
│ scout-platform/ (umbrella chart)                            │
│ ├── Chart.yaml (dependencies on all subcharts)             │
│ ├── values.yaml (defaults for all services)                │
│ ├── values-onprem.yaml (on-prem overrides)                 │
│ ├── values-aws.yaml (AWS overrides)                        │
│ └── charts/ (subcharts)                                    │
│     ├── infrastructure/ (optional components)               │
│     │   ├── cnpg (PostgreSQL - can disable for RDS)        │
│     │   └── minio (S3 - can disable for AWS S3)            │
│     ├── data-layer/                                         │
│     │   ├── hive-metastore                                  │
│     │   ├── cassandra (can disable for AWS Keyspaces)       │
│     │   └── elasticsearch                                   │
│     ├── orchestration/                                      │
│     │   └── temporal                                        │
│     ├── analytics/                                          │
│     │   ├── trino                                           │
│     │   └── superset                                        │
│     ├── ingestion/                                          │
│     │   ├── hl7log-extractor                                │
│     │   └── hl7-transformer                                 │
│     ├── auth/                                               │
│     │   ├── keycloak                                        │
│     │   └── oauth2-proxy                                    │
│     ├── notebooks/                                          │
│     │   └── jupyterhub                                      │
│     ├── monitoring/                                         │
│     │   ├── prometheus                                      │
│     │   ├── loki                                            │
│     │   └── grafana                                         │
│     └── ui/                                                 │
│         └── launchpad                                       │
│                                                              │
│ Deploy: helm install scout-platform ./scout-platform \     │
│         -f values-aws.yaml                                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Orchestration (Optional)                           │
│                                                              │
│ Option A: Helmfile (declarative, simple)                   │
│ Option B: ArgoCD (GitOps, UI, multi-cluster)               │
│ Option C: Flux (GitOps, Kubernetes-native)                 │
│                                                              │
│ Purpose: Manage multiple Scout instances, handle           │
│ complex dependency ordering, progressive rollouts           │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Umbrella Chart Structure

**Chart.yaml** (scout-platform umbrella):
```yaml
apiVersion: v2
name: scout-platform
version: 2.0.0
description: Scout platform for healthcare data exploration
type: application

dependencies:
  # Infrastructure (optional - can use cloud services)
  - name: cloudnative-pg
    repository: https://cloudnative-pg.github.io/charts
    version: ~0.22.0
    condition: postgresql.enabled
    tags: [infrastructure]

  - name: minio-operator
    repository: https://operator.min.io
    version: ~6.0.0
    condition: minio.enabled
    tags: [infrastructure]

  # Data Layer
  - name: hive-metastore
    repository: file://charts/hive-metastore
    version: 1.0.0
    condition: hive.enabled
    tags: [data-layer]

  - name: cass-operator
    repository: https://helm.k8ssandra.io/stable
    version: ~0.50.0
    condition: cassandra.enabled
    tags: [data-layer]

  - name: eck-operator
    repository: https://helm.elastic.co
    version: ~2.16.0
    condition: elasticsearch.enabled
    tags: [data-layer]

  # Orchestration
  - name: temporal
    repository: https://go.temporal.io/helm-charts
    version: ~1.0.0
    condition: temporal.enabled
    tags: [orchestration]

  # Analytics
  - name: trino
    repository: https://trinodb.github.io/charts
    version: ~0.35.0
    condition: trino.enabled
    tags: [analytics]

  - name: superset
    repository: https://apache.github.io/superset
    version: ~0.17.0
    condition: superset.enabled
    tags: [analytics]

  # Ingestion (in-house)
  - name: hl7log-extractor
    repository: file://charts/extractor/hl7log-extractor
    version: 1.1.0
    condition: extractor.enabled
    tags: [ingestion]

  - name: hl7-transformer
    repository: file://charts/extractor/hl7-transformer
    version: 1.1.0
    condition: extractor.enabled
    tags: [ingestion]

  # UI
  - name: launchpad
    repository: file://charts/launchpad
    version: 1.1.0
    condition: launchpad.enabled
    tags: [ui]

  # ... other dependencies
```

**Key Features:**
- **Conditional Dependencies**: Use `condition` to enable/disable charts (e.g., `postgresql.enabled: false` for AWS RDS)
- **Tags**: Group related charts for bulk enable/disable (e.g., `--set tags.infrastructure=false`)
- **Version Ranges**: Use `~1.2.3` for patch-level updates (>= 1.2.3, < 1.3.0)
- **Local Charts**: Use `file://` for in-repo charts

### 2.3 Configuration Strategy

**values.yaml** (defaults for all environments):
```yaml
# Global settings shared across all subcharts
global:
  domain: scout.example.com
  timezone: America/Chicago
  imageRegistry: ghcr.io/washu-tag
  imagePullPolicy: IfNotPresent

  # Storage backend (s3 or minio)
  storage:
    type: s3
    endpoint: ""  # Empty for AWS S3, set for MinIO
    region: us-east-1
    accessKey: ""
    secretKey: ""
    buckets:
      lake: scout-lake
      loki: scout-loki
      temporal: scout-temporal

  # Database backend (rds or postgresql)
  database:
    type: postgresql
    host: postgres-cnpg-rw.cnpg.svc.cluster.local
    port: 5432
    # Databases created: keycloak, superset, hive, ingest

  # Authentication
  auth:
    keycloak:
      enabled: true
      url: https://auth.scout.example.com

# Infrastructure components (optional)
postgresql:
  enabled: true  # Set to false for AWS RDS
  cluster:
    instances: 3
    storage:
      storageClass: ebs-sc  # Override per environment
      size: 100Gi

minio:
  enabled: true  # Set to false for AWS S3
  tenant:
    pools:
      - servers: 4
        volumesPerServer: 2
        size: 1Ti
        storageClassName: ebs-sc

# Data layer
hive:
  enabled: true
  metastore:
    warehouse: s3a://scout-lake/delta

cassandra:
  enabled: true  # Set to false for AWS Keyspaces
  size: 3
  storage:
    storageClass: ebs-sc
    size: 300Gi

# ... other services
```

**values-aws.yaml** (AWS overrides):
```yaml
# Disable self-hosted infrastructure, use AWS services
postgresql:
  enabled: false

minio:
  enabled: false

cassandra:
  enabled: false

global:
  storage:
    type: s3
    endpoint: ""  # Use AWS S3
    region: us-east-1
    buckets:
      lake: scout-lake-prod
      loki: scout-loki-prod

  database:
    type: rds
    host: scout-postgres.abc123.us-east-1.rds.amazonaws.com
    port: 5432

  cassandra:
    type: keyspaces
    host: cassandra.us-east-1.amazonaws.com
    port: 9142

# Use EBS for storage
storageClass: gp3

# Use AWS Load Balancer Controller for ingress
ingress:
  class: alb
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
```

**values-onprem.yaml** (On-premise overrides):
```yaml
# Enable all infrastructure components
postgresql:
  enabled: true
  cluster:
    storage:
      storageClass: local-path

minio:
  enabled: true
  tenant:
    pools:
      - servers: 4
        volumesPerServer: 2
        size: 750Gi
        storageClassName: local-path

global:
  storage:
    type: minio
    endpoint: https://minio.scout.local:9000

  database:
    type: postgresql
    host: postgres-cnpg-rw.cnpg.svc.cluster.local

# Use local-path for storage
storageClass: local-path

# Use Traefik for ingress
ingress:
  class: traefik
```

---

## 3. Breaking Changes and Migration Path

### 3.1 What Migrates Seamlessly

✅ **These components can move to umbrella chart with minimal changes:**

1. **External Helm Charts** - Already using Helm, just change from Ansible to umbrella dependencies
   - PostgreSQL (CNPG)
   - MinIO
   - Temporal
   - Trino
   - Superset
   - JupyterHub
   - Prometheus, Loki, Grafana

2. **In-House Charts** - Already have Helm charts, just move to subcharts/
   - launchpad
   - hl7log-extractor
   - hl7-transformer
   - hive-metastore

3. **Keycloak Configuration** - Can use Helm post-install Jobs instead of Ansible tasks
   - Realm creation
   - Client creation
   - OIDC provider setup

### 3.2 Breaking Changes Required

❌ **These components require significant redesign:**

#### **1. Storage Provisioning**

**Current Approach:**
```yaml
# Ansible creates directories on nodes
- name: Create postgres directory
  file:
    path: /scout/persistence/postgres
    state: directory
  delegate_to: "{{ node_url }}"

# Then creates PV pointing to that directory
- name: Create postgres PV
  kubernetes.core.k8s:
    definition:
      kind: PersistentVolume
      spec:
        local:
          path: /scout/persistence/postgres
        nodeAffinity:
          required:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: kubernetes.io/hostname
                    operator: In
                    values: [specific-node]
```

**Problem**: Helm cannot create directories on nodes or create node-affinitive PVs with specific paths.

**Solution Options:**

**Option A: Dynamic Provisioning (Recommended)**
- Use CSI drivers that support dynamic provisioning
  - **AWS**: EBS CSI driver (already in Terraform code)
  - **On-Prem**: local-path-provisioner (built into K3s), OpenEBS, Rook-Ceph, Longhorn
- Benefits: Fully portable, no manual PV creation
- Tradeoffs: Requires CSI driver installation (add to Layer 1)

**Option B: Pre-Provisioning with Ansible**
- Keep Ansible role for creating directories and PVs (run before Helm)
- Helm charts just reference pre-created storage class
- Benefits: Works with existing infrastructure
- Tradeoffs: Still requires Ansible for storage setup

**Option C: Kubernetes Operators**
- Use operators that handle storage (e.g., CNPG creates its own PVCs)
- Benefits: Self-managing
- Tradeoffs: Requires operator installation, less control

**Recommended**: Option A for new deployments, Option B for migration phase.

#### **2. Keycloak Deployment**

**Current Approach:**
- Deploys Keycloak Operator via raw YAML manifests (not Helm)
- Uses Ansible tasks to configure realms, clients, identity providers

**Problem**: Keycloak Operator doesn't have an official Helm chart.

**Solutions:**

**Option A: Community Helm Chart**
- Use codecentric/keycloak chart or bitnami/keycloak
- Use Helm post-install Jobs with keycloak-config-cli for configuration
- Benefits: Standard Helm approach
- Tradeoffs: Need to validate compatibility

**Option B: Helm Template for Operator**
- Create Helm chart that wraps the operator YAML
- Include realm/client CRs as templates
- Benefits: Keeps operator approach, adds Helm benefits
- Tradeoffs: Must maintain wrapper chart

**Option C: Use External Keycloak**
- Treat Keycloak as external dependency (like AWS RDS)
- Configure via terraform or separate Ansible playbook
- Benefits: Separates identity management from application
- Tradeoffs: More complex architecture

**Recommended**: Option B (Helm wrapper for operator) for on-prem, Option C for AWS.

#### **3. OAuth2-Proxy Deployment**

**Current Approach:**
- Deployed to `kube-system` namespace
- Creates Traefik middlewares for authentication

**Problem**: Umbrella chart deploys to single namespace, oauth2-proxy needs to be in same namespace as apps OR use IngressClass-level auth.

**Solutions:**

**Option A: Deploy per Application**
- Each service that needs auth deploys its own oauth2-proxy sidecar
- Benefits: Namespace isolation
- Tradeoffs: Resource overhead

**Option B: Shared OAuth2-Proxy in Scout Namespace**
- Deploy oauth2-proxy as part of umbrella chart
- All ingresses reference it via ExternalName service or annotation
- Benefits: Single instance, less overhead
- Tradeoffs: Requires cross-namespace service reference

**Option C: Gateway API / Istio**
- Use service mesh for authentication
- Benefits: Modern, standard approach
- Tradeoffs: Adds complexity, requires mesh installation

**Recommended**: Option B (shared oauth2-proxy in scout namespace).

#### **4. Air-Gapped Deployment**

**Current Approach:**
- Ansible detects `air_gapped: true`
- Configures K3s registry mirrors in `/etc/rancher/k3s/registries.yaml`
- Deploys Harbor on staging node
- Helm runs on localhost (Ansible control node) instead of cluster

**Problem**: Registry mirror configuration requires host-level files, Harbor deployment is separate.

**Solutions:**

**Option A: Keep Air-Gap in Layer 1**
- K3s installation (Ansible/Terraform) handles registry mirrors
- Harbor deployed separately (or use existing registry)
- Helm charts reference images normally; K3s rewrites them
- Benefits: Clean separation, charts unchanged
- Tradeoffs: Requires Layer 1 setup

**Option B: OCI Chart Distribution**
- Package umbrella chart as OCI artifact
- Push to Harbor/internal registry
- Deploy with `helm install oci://harbor.local/scout/scout-platform`
- Benefits: Fully air-gapped, no internet required
- Tradeoffs: Requires OCI registry

**Recommended**: Option A for infrastructure, Option B for chart distribution.

#### **5. Multi-Instance Hive Metastore**

**Current Approach:**
- Deploys same chart twice with different release names: `hive-metastore` and `hive-metastore-readonly`
- Different credentials (write vs read-only S3 access)

**Problem**: Umbrella chart dependencies deploy once per chart.

**Solutions:**

**Option A: Alias Dependencies**
```yaml
dependencies:
  - name: hive-metastore
    alias: hive-metastore-write
    repository: file://charts/hive-metastore
    version: 1.0.0

  - name: hive-metastore
    alias: hive-metastore-readonly
    repository: file://charts/hive-metastore
    version: 1.0.0
```
- Benefits: Standard Helm pattern
- Tradeoffs: Must configure each instance separately in values

**Option B: Single Chart with Multiple Instances**
- Modify hive-metastore chart to support multiple instances via values
```yaml
hive:
  instances:
    - name: write
      credentials: s3_lake_writer
    - name: readonly
      credentials: s3_lake_reader
```
- Benefits: Single dependency, easier to manage
- Tradeoffs: More complex chart template

**Recommended**: Option A (aliases) for simplicity.

### 3.3 Migration Path

**Phase 1: Parallel Deployment (No Breaking Changes)**
1. Create umbrella chart with all dependencies
2. Deploy via Ansible using new wrapper role that runs `helm install scout-platform`
3. Keep existing Ansible roles for infrastructure (K3s, storage, etc.)
4. Validate parity with current deployment

**Phase 2: Separate Infrastructure (Minor Breaking Changes)**
1. Create separate Ansible playbooks for infrastructure vs application
2. Infrastructure playbook: `infra.yaml` (K3s, storage, TLS)
3. Application playbook: `app.yaml` (just runs `helm install`)
4. Document prerequisites for AWS deployment (EKS cluster, EBS CSI, etc.)

**Phase 3: Cloud Migration (Breaking Changes)**
1. Create AWS Terraform module that provisions EKS + dependencies
2. Create `values-aws.yaml` with RDS/S3 configuration
3. Test deployment on EKS
4. Document differences and migration steps

**Phase 4: Full Portability**
1. Remove Ansible from application deployment entirely
2. Use Helmfile or ArgoCD for orchestration
3. Publish umbrella chart to OCI registry
4. Support one-command deployment: `helm install scout-platform oci://registry/scout/scout-platform -f values-aws.yaml`

---

## 4. Dependency Ordering Solutions

### 4.1 The Problem

**Current Ansible Approach:**
- Sequential playbooks ensure dependencies ready before dependents
- Example: `postgres.yaml` runs before `extractor.yaml`

**Helm Umbrella Chart Behavior:**
- All subcharts deploy in parallel
- Helm sorts resources by type (Namespace → Secret → ConfigMap → Deployment → Service → Ingress)
- **No guarantee** that PostgreSQL is ready before hl7log-extractor tries to connect

### 4.2 Solution 1: Helm Hooks

Helm provides hooks to run Jobs at specific lifecycle points:

**Hook Types:**
- `pre-install` - Before any resources created
- `post-install` - After all resources created
- `pre-upgrade` - Before any resources updated
- `post-upgrade` - After all resources updated

**Hook Weight:**
- Controls execution order among hooks
- Lower weight runs first
- `helm.sh/hook-weight: "-5"` runs before `helm.sh/hook-weight: "5"`

**Example: Wait-for-PostgreSQL Job**

In `hl7log-extractor/templates/wait-for-postgres-job.yaml`:
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "hl7log-extractor.fullname" . }}-wait-postgres
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  template:
    spec:
      containers:
        - name: wait-postgres
          image: postgres:16
          command:
            - sh
            - -c
            - |
              until pg_isready -h {{ .Values.database.host }} -p {{ .Values.database.port }} -U postgres; do
                echo "Waiting for PostgreSQL..."
                sleep 5
              done
              echo "PostgreSQL is ready!"
      restartPolicy: OnFailure
  backoffLimit: 30
```

**Benefits:**
- Native Helm solution
- Well-understood pattern
- Works with any orchestration tool

**Tradeoffs:**
- Must create wait Jobs for each dependency
- Can increase deployment time
- Jobs add complexity to charts

### 4.3 Solution 2: Init Containers

Applications can wait for dependencies using init containers:

**Example: hl7log-extractor waits for PostgreSQL and Temporal**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hl7log-extractor
spec:
  template:
    spec:
      initContainers:
        - name: wait-postgres
          image: postgres:16
          command:
            - sh
            - -c
            - |
              until pg_isready -h $DB_HOST -p $DB_PORT; do
                echo "Waiting for PostgreSQL..."
                sleep 5
              done
          env:
            - name: DB_HOST
              value: {{ .Values.database.host }}
            - name: DB_PORT
              value: "{{ .Values.database.port }}"

        - name: wait-temporal
          image: curlimages/curl:latest
          command:
            - sh
            - -c
            - |
              until curl -f http://temporal-frontend.temporal.svc.cluster.local:7233; do
                echo "Waiting for Temporal..."
                sleep 5
              done

      containers:
        - name: hl7log-extractor
          # ... main container
```

**Benefits:**
- Standard Kubernetes pattern
- Retries built into pod lifecycle
- No extra Jobs to manage

**Tradeoffs:**
- Application-level solution (each app handles own dependencies)
- Pod won't be "Running" until init containers succeed
- Harder to debug (must check init container logs)

### 4.4 Solution 3: Application-Level Retry

Spring Boot, Temporal, and most frameworks support connection retry:

**Example: Spring Boot application.yaml**
```yaml
spring:
  datasource:
    url: jdbc:postgresql://postgres-cnpg-rw.cnpg.svc.cluster.local:5432/ingest
    hikari:
      connection-timeout: 30000
      initialization-fail-timeout: -1  # Retry forever
      maximum-pool-size: 10

  retry:
    max-attempts: 30
    backoff:
      delay: 5s
      max-delay: 60s
```

**Benefits:**
- No Kubernetes-specific code
- Application handles transient failures at runtime
- Works for both startup and runtime failures

**Tradeoffs:**
- Pod shows as "Running" but may not be healthy
- Requires application framework support
- May delay detection of configuration errors

### 4.5 Solution 4: Readiness Probes

Ensure services aren't marked "ready" until dependencies available:

**Example: Temporal waits for Cassandra**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: temporal-frontend
spec:
  template:
    spec:
      containers:
        - name: temporal
          readinessProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - |
                  # Check if Temporal is healthy
                  curl -f http://localhost:7233/health
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 30
```

**Benefits:**
- Standard Kubernetes pattern
- Service discovery respects readiness
- Prevents traffic to unhealthy pods

**Tradeoffs:**
- Doesn't prevent pod creation, just marks as not ready
- Services that don't have health endpoints need custom checks

### 4.6 Solution 5: Helmfile Ordering

Helmfile supports `needs` to enforce ordering:

**helmfile.yaml:**
```yaml
releases:
  - name: cnpg-operator
    namespace: cnpg-operator
    chart: cnpg/cloudnative-pg

  - name: postgres
    namespace: cnpg
    chart: cnpg/cluster
    needs:
      - cnpg-operator/cnpg-operator

  - name: keycloak
    namespace: keycloak
    chart: ./charts/keycloak
    needs:
      - cnpg/postgres

  - name: temporal
    namespace: temporal
    chart: temporal/temporal
    needs:
      - cnpg/postgres
      - cassandra/cassandra
      - elastic/elasticsearch

  - name: extractor
    namespace: extractor
    chart: ./charts/hl7log-extractor
    needs:
      - cnpg/postgres
      - temporal/temporal
      - minio/minio
```

**Benefits:**
- Explicit dependency graph
- Helmfile waits for `needs` releases to be ready before proceeding
- Works with any Helm charts (no modifications required)

**Tradeoffs:**
- Adds orchestration layer
- Sequential deployment can be slower
- Must manually define all dependencies

### 4.7 Solution 6: ArgoCD Sync Waves

ArgoCD supports phased deployment using annotations:

**Phase 1: Infrastructure (wave 0)**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: cnpg-operator
  annotations:
    argocd.argoproj.io/sync-wave: "0"
```

**Phase 2: Databases (wave 1)**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: postgres
  annotations:
    argocd.argoproj.io/sync-wave: "1"
```

**Phase 3: Applications (wave 2)**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: extractor
  annotations:
    argocd.argoproj.io/sync-wave: "2"
```

**Benefits:**
- Native ArgoCD feature
- Visualizes dependency waves in UI
- Supports health checks before proceeding

**Tradeoffs:**
- Requires ArgoCD
- Must annotate all resources
- Less flexible than Helmfile `needs`

### 4.8 Recommended Approach

**Multi-Layered Strategy:**

1. **Use Init Containers + Application Retry** (no orchestration tool required)
   - All services have init containers to wait for critical dependencies
   - Applications configure retry/backoff for transient failures
   - Benefit: Works with bare `helm install`, no orchestration needed

2. **Add Helmfile for Complex Deployments** (recommended for production)
   - Use Helmfile `needs` to enforce high-level ordering
   - Example: `postgres → keycloak → all other services`
   - Benefit: Faster convergence, easier troubleshooting

3. **Use ArgoCD for GitOps** (optional, for multi-environment/multi-cluster)
   - Sync waves for phased rollout
   - Health checks before proceeding
   - Benefit: Best for CD pipelines, multi-tenant clusters

**Concrete Example:**

```yaml
# helmfile.yaml for Scout
releases:
  # Wave 1: Infrastructure Operators
  - name: cnpg-operator
    namespace: cnpg-operator
    chart: cnpg/cloudnative-pg
    version: 0.22.0

  - name: minio-operator
    namespace: minio-operator
    chart: minio-operator/operator
    version: 6.0.0

  # Wave 2: Stateful Infrastructure (wait for operators)
  - name: postgres
    namespace: cnpg
    chart: cnpg/cluster
    needs: [cnpg-operator/cnpg-operator]
    wait: true
    timeout: 600

  - name: minio
    namespace: minio-scout
    chart: minio-operator/tenant
    needs: [minio-operator/minio-operator]
    wait: true

  - name: cassandra
    namespace: cassandra
    chart: k8ssandra/cass-operator
    wait: true

  # Wave 3: Auth (depends on postgres)
  - name: keycloak
    namespace: keycloak
    chart: ./charts/keycloak
    needs: [cnpg/postgres]
    wait: true

  # Wave 4: Main Application (umbrella chart)
  - name: scout-platform
    namespace: scout
    chart: ./charts/scout-platform
    needs:
      - cnpg/postgres
      - minio-scout/minio
      - cassandra/cassandra
      - keycloak/keycloak
    values:
      - values-{{ .Environment.Name }}.yaml
    wait: true
    timeout: 1200
```

---

## 5. Storage Portability Strategy

### 5.1 Current Storage Approach

**Storage Requirements by Service:**

| Service | Storage Type | Size (Prod) | Access Pattern | Node Affinity |
|---------|--------------|-------------|----------------|---------------|
| PostgreSQL | Block | 100Gi+ | RWO | Single node |
| MinIO | Block | 750Gi per volume × 8 | RWO | Specific nodes |
| Cassandra | Block | 300Gi | RWO | Single node |
| Elasticsearch | Block | 100Gi | RWO | Single node |
| Jupyter | Block | 250Gi | RWX (multi-user) | Any |
| Prometheus | Block | 100Gi | RWO | Single node |
| Loki | Block | Minimal (uses S3) | RWO | Single node |
| Grafana | Block | 50Gi | RWO | Single node |

**Current Provisioning (On-Prem):**
1. Ansible creates directories on specific nodes (e.g., `/scout/data/minio0` on `node1`)
2. Ansible creates PV with `local` volume source + node affinity
3. Helm chart creates PVC with specific storage class
4. Kubernetes binds PVC to PV based on storage class + capacity + node

**Problem**: Cannot create node-affinitive PVs from Helm.

### 5.2 Portability Patterns

#### **Pattern 1: Dynamic Provisioning with CSI Drivers (Recommended)**

**On-Premise Options:**

| CSI Driver | Use Case | Pros | Cons |
|------------|----------|------|------|
| **local-path-provisioner** | Single-node or RWO workloads | Simple, built into K3s, no config | No replication, node failure = data loss |
| **Longhorn** | Replicated block storage | Replicated (HA), web UI, snapshots | Requires extra nodes, more complex |
| **Rook-Ceph** | Enterprise storage | Highly available, mature, S3-compatible | Complex setup, resource-intensive |
| **OpenEBS** | Variety (local, replicated, CSI) | Flexible, modular architecture | Multiple components, learning curve |
| **NFS provisioner** | Shared storage (RWX) | Simple, works with existing NAS | Single point of failure, slower |

**Cloud Options:**

| Cloud | CSI Driver | Storage Class | Features |
|-------|------------|---------------|----------|
| AWS | EBS CSI | `gp3`, `io2` | Auto-provisioning, snapshots, encryption, volume expansion |
| AWS | EFS CSI | `efs-sc` | RWX support, managed, multi-AZ |
| Azure | Azure Disk CSI | `managed-premium` | Managed, snapshots, encryption |
| GCP | GCE PD CSI | `standard-rwo` | Managed, snapshots, regional disks |

**Recommended Architecture:**

```yaml
# values.yaml (defaults)
global:
  storageClass: ""  # Auto-detect default SC

postgresql:
  enabled: true
  cluster:
    storage:
      storageClass: ""  # Inherit from global, or override
      size: 100Gi

# values-aws.yaml
global:
  storageClass: gp3

postgresql:
  cluster:
    storage:
      size: 200Gi  # Larger for production

# values-onprem-ha.yaml (with Longhorn)
global:
  storageClass: longhorn

postgresql:
  cluster:
    storage:
      size: 100Gi

# values-onprem-simple.yaml (with local-path)
global:
  storageClass: local-path

postgresql:
  cluster:
    storage:
      size: 100Gi
```

**Benefits:**
- Fully portable: Same Helm chart works on all platforms
- No manual PV creation
- Supports volume expansion, snapshots, encryption (depending on CSI driver)

**Tradeoffs:**
- On-prem requires CSI driver installation (add to Layer 1)
- Local-path not replicated (acceptable for dev, not for prod)
- Longhorn/Ceph adds complexity

#### **Pattern 2: Pre-Provisioned Storage**

Keep Ansible for storage setup, Helm just references it:

**Ansible playbook (run before Helm):**
```yaml
- name: Create storage infrastructure
  hosts: k3s_cluster
  tasks:
    - name: Create PVs
      kubernetes.core.k8s:
        definition:
          apiVersion: v1
          kind: PersistentVolume
          metadata:
            name: postgres-pv
          spec:
            capacity:
              storage: 100Gi
            volumeMode: Filesystem
            accessModes: [ReadWriteOnce]
            persistentVolumeReclaimPolicy: Retain
            storageClassName: manual
            local:
              path: /scout/persistence/postgres
            nodeAffinity:
              required:
                nodeSelectorTerms:
                  - matchExpressions:
                      - key: kubernetes.io/hostname
                        operator: In
                        values: [{{ node_url }}]
```

**Helm values:**
```yaml
postgresql:
  cluster:
    storage:
      storageClass: manual  # References pre-created PVs
```

**Benefits:**
- Works with existing infrastructure
- No new dependencies
- Fine-grained control over storage layout

**Tradeoffs:**
- Not portable to cloud (must change to dynamic provisioning for AWS)
- Still requires Ansible

#### **Pattern 3: External Storage Services**

Use managed services instead of in-cluster storage:

**AWS Example:**
```yaml
# values-aws.yaml
postgresql:
  enabled: false  # Don't deploy CNPG

minio:
  enabled: false  # Don't deploy MinIO

global:
  database:
    host: scout-postgres.abc123.us-east-1.rds.amazonaws.com
    port: 5432
    username: scout_admin
    existingSecret: rds-credentials  # Created by Terraform

  storage:
    type: s3
    endpoint: ""  # Use AWS S3 (no endpoint)
    region: us-east-1
    existingSecret: s3-credentials  # IAM role or secret
```

**Benefits:**
- No storage management
- Fully managed backups, HA, scaling
- Better performance (AWS RDS > self-hosted PostgreSQL for most cases)

**Tradeoffs:**
- Vendor lock-in
- Higher cost
- Less control

### 5.3 MinIO-Specific Challenges

MinIO Operator requires multiple volumes per server for performance:

**Current Ansible:**
```yaml
minio_hosts:
  - hostname: node1
    volumes: [minio0, minio1]
  - hostname: node2
    volumes: [minio0, minio1]
  - hostname: node3
    volumes: [minio0, minio1]
  - hostname: node4
    volumes: [minio0, minio1]

# Creates 8 PVs total (4 nodes × 2 volumes) with node affinity
```

**Helm Equivalent (Dynamic Provisioning):**
```yaml
# values.yaml
minio:
  tenant:
    pools:
      - servers: 4
        volumesPerServer: 2
        size: 750Gi
        storageClass: gp3  # AWS auto-provisions 8 volumes
```

**For On-Prem with Node Affinity:**
- Use Longhorn/Rook-Ceph with dynamic provisioning
- OR keep Ansible for PV creation (non-portable)

### 5.4 Recommended Strategy

**By Environment:**

| Environment | Storage Strategy | Rationale |
|-------------|------------------|-----------|
| **AWS EKS (Production)** | Managed services (RDS, S3) + EBS CSI | Fully managed, HA, backups, no maintenance |
| **On-Prem HA (Production)** | Longhorn + MinIO | Replicated storage, good performance, open-source |
| **On-Prem Single-Node (Dev)** | local-path + MinIO | Simple, fast, acceptable for non-HA |
| **Laptop/CI (Dev)** | local-path | Built into K3s, zero config |

**Migration Path:**

1. **Phase 1 (Now)**: Keep Ansible for storage, Helm for apps
2. **Phase 2 (3 months)**: Add Longhorn to on-prem, use dynamic provisioning
3. **Phase 3 (6 months)**: Create AWS values file with RDS/S3, test deployment
4. **Phase 4 (9 months)**: Publish multiple deployment guides (on-prem, AWS, Azure, GCP)

---

## 6. Namespace Strategy

### 6.1 Current Multi-Namespace Architecture

**Problem Statement:**
- Current Ansible deploys services to 15+ namespaces
- Umbrella chart deploys all subcharts to single namespace
- Services reference each other across namespaces (e.g., `hive-metastore.hive.svc.cluster.local`)

**Current Namespaces:**

| Namespace | Services | Reason for Separate Namespace |
|-----------|----------|-------------------------------|
| `cnpg-operator` | CNPG operator | Operator pattern (cluster-wide) |
| `cnpg` | PostgreSQL cluster | Separate from operator |
| `minio-operator` | MinIO operator | Operator pattern (cluster-wide) |
| `minio-scout` | MinIO tenant | Tenant isolation |
| `cassandra` | Cassandra | Temporal backend isolation |
| `elastic` | Elasticsearch | Temporal visibility isolation |
| `temporal` | Temporal | Orchestration layer |
| `hive` | Hive Metastore | Data layer |
| `trino` | Trino | Query engine |
| `superset` | Superset | Analytics |
| `extractor` | HL7 services | Ingestion layer |
| `jupyter` | JupyterHub | Notebooks |
| `keycloak` | Keycloak | Auth |
| `prometheus`, `loki`, `grafana` | Monitoring | Observability |
| `launchpad` | Landing page | UI |

### 6.2 Options for Namespace Management

#### **Option A: Single Namespace (Simplest)**

Deploy all services to `scout` namespace:

```yaml
# helm install scout-platform ./scout-platform -n scout
```

**Pros:**
- Simplest deployment
- No cross-namespace service references
- NetworkPolicies easier to manage
- Easier RBAC (single ServiceAccount can access all services)

**Cons:**
- Less isolation (all services in one namespace)
- Harder to manage RBAC for multi-tenant clusters
- Operator namespaces still separate (e.g., `cnpg-operator`)

**When to Use:**
- Single-tenant clusters
- Development environments
- Small teams

#### **Option B: Logical Grouping (Recommended)**

Group services by layer:

| Namespace | Services | Purpose |
|-----------|----------|---------|
| `scout-operators` | CNPG operator, MinIO operator, ECK operator, cert-manager | Cluster-wide operators |
| `scout-infrastructure` | PostgreSQL, MinIO, Cassandra, Elasticsearch | Stateful infrastructure |
| `scout-platform` | Temporal, Hive, Trino, Keycloak, OAuth2-Proxy | Platform services |
| `scout-applications` | Extractor, Superset, JupyterHub, Launchpad | User-facing applications |
| `scout-monitoring` | Prometheus, Loki, Grafana | Observability |

**Helm Implementation:**

```yaml
# helmfile.yaml
releases:
  - name: cnpg-operator
    namespace: scout-operators
    chart: cnpg/cloudnative-pg

  - name: postgres
    namespace: scout-infrastructure
    chart: cnpg/cluster

  - name: temporal
    namespace: scout-platform
    chart: temporal/temporal
    values:
      - postgresql:
          host: postgres-cnpg-rw.scout-infrastructure.svc.cluster.local

  - name: extractor
    namespace: scout-applications
    chart: ./charts/hl7log-extractor
    values:
      - database:
          host: postgres-cnpg-rw.scout-infrastructure.svc.cluster.local
      - temporal:
          host: temporal-frontend.scout-platform.svc.cluster.local
```

**Pros:**
- Logical separation
- Better multi-tenancy (can deploy multiple Scout instances in same cluster)
- Clear ownership boundaries

**Cons:**
- Must update all service references to include namespace (`.svc.cluster.local`)
- More complex NetworkPolicies
- More namespaces to manage

**When to Use:**
- Multi-tenant clusters
- Production environments
- Large teams with ownership boundaries

#### **Option C: Per-Service Namespaces (Current)**

Keep current 15+ namespace structure:

**Helm Implementation:**

Must use Helmfile or ArgoCD to deploy to multiple namespaces:

```yaml
# helmfile.yaml
releases:
  - name: postgres
    namespace: cnpg
    chart: cnpg/cluster

  - name: hive-metastore
    namespace: hive
    chart: ./charts/hive-metastore

  - name: temporal
    namespace: temporal
    chart: temporal/temporal

  # ... 15 more releases
```

**Pros:**
- Maximum isolation
- Matches current architecture (easier migration)
- Fine-grained RBAC

**Cons:**
- Most complex to manage
- Many cross-namespace service references
- Harder to reason about

**When to Use:**
- Strict compliance requirements
- Very large installations with dedicated teams per service

### 6.3 Recommendation

**For Portability: Option B (Logical Grouping)**

```yaml
# helmfile.yaml structure
releases:
  # Layer 1: Operators (cluster-scoped)
  - name: operators
    namespace: scout-operators
    chart: ./charts/scout-operators  # Umbrella chart for all operators
    values:
      - cnpg:
          enabled: true
      - minio-operator:
          enabled: true
      - eck-operator:
          enabled: {{ .Values.elasticsearch.enabled }}

  # Layer 2: Infrastructure (stateful)
  - name: infrastructure
    namespace: scout-infrastructure
    chart: ./charts/scout-infrastructure
    needs: [scout-operators/operators]
    values:
      - values-{{ .Environment.Name }}.yaml

  # Layer 3: Platform (business logic)
  - name: platform
    namespace: scout-platform
    chart: ./charts/scout-platform
    needs: [scout-infrastructure/infrastructure]
    values:
      - values-{{ .Environment.Name }}.yaml

  # Layer 4: Applications (user-facing)
  - name: applications
    namespace: scout-applications
    chart: ./charts/scout-applications
    needs: [scout-platform/platform]
    values:
      - values-{{ .Environment.Name }}.yaml
```

**Benefits:**
- Clear architectural layers
- Supports multi-tenancy (deploy multiple Scout instances: `scout-dev-*`, `scout-prod-*`)
- Reasonable number of namespaces (4 instead of 15+)
- Matches Helm umbrella chart pattern (can create 4 umbrella charts)

### 6.4 Breaking Changes

**Service References Must Change:**

**Before (Ansible):**
```yaml
# Services in same namespace or using short names
database:
  host: postgres-cnpg-rw  # Assumes same namespace
```

**After (Multi-Namespace):**
```yaml
# Must use FQDN
database:
  host: postgres-cnpg-rw.scout-infrastructure.svc.cluster.local
```

**Solution: Global Values**

```yaml
# values.yaml
global:
  namespaces:
    operators: scout-operators
    infrastructure: scout-infrastructure
    platform: scout-platform
    applications: scout-applications

  # Pre-compute FQDNs
  endpoints:
    postgres: postgres-cnpg-rw.{{ .Values.global.namespaces.infrastructure }}.svc.cluster.local
    minio: minio.{{ .Values.global.namespaces.infrastructure }}.svc.cluster.local
    temporal: temporal-frontend.{{ .Values.global.namespaces.platform }}.svc.cluster.local
    hive: hive-metastore.{{ .Values.global.namespaces.platform }}.svc.cluster.local

# In hl7log-extractor/templates/deployment.yaml
env:
  - name: SPRING_DATASOURCE_URL
    value: jdbc:postgresql://{{ .Values.global.endpoints.postgres }}:5432/ingest
  - name: TEMPORAL_HOST
    value: {{ .Values.global.endpoints.temporal }}
```

---

## 7. Orchestration Tool Comparison

### 7.1 Comparison Matrix

| Feature | **Bare Helm** | **Helmfile** | **ArgoCD** | **Flux CD** |
|---------|---------------|--------------|------------|-------------|
| **Deployment Model** | Imperative CLI | Declarative YAML | GitOps (declarative) | GitOps (declarative) |
| **Dependency Ordering** | ❌ No (parallel only) | ✅ Yes (`needs`) | ✅ Yes (sync waves) | ✅ Yes (depends-on) |
| **Multi-Environment** | Manual values files | ✅ Environments | ✅ ApplicationSets | ✅ Kustomize overlays |
| **Multi-Cluster** | Manual kubeconfig switching | ✅ Context switching | ✅ Native multi-cluster | ✅ Native multi-cluster |
| **UI** | ❌ None | ❌ None | ✅ Web UI | ❌ None (CLI only) |
| **Helm Native** | ✅ Yes | ✅ Yes (wraps Helm CLI) | ⚠️ Template-only (no hooks) | ✅ Yes (native HelmRelease) |
| **Secret Management** | Manual | ✅ helm-secrets plugin | ✅ External Secrets Operator | ✅ Native SOPS integration |
| **Diff Preview** | ❌ No (requires plugin) | ✅ Built-in | ✅ UI-based diff | ✅ CLI diff |
| **Rollback** | ✅ `helm rollback` | ✅ `helmfile sync` | ✅ UI/CLI rollback | ✅ Git revert |
| **Learning Curve** | Low | Low | Medium | Medium-High |
| **Resource Usage** | None (CLI) | None (CLI) | Medium (server-side) | Low (controllers) |
| **Air-Gap Support** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **CI/CD Integration** | ✅ Easy | ✅ Easy | ⚠️ Pull-based (webhook) | ⚠️ Pull-based (webhook) |
| **Maturity** | Very mature | Mature | Very mature | Mature |

### 7.2 Tool Deep-Dive

#### **Helmfile**

**What It Is:**
- Declarative wrapper around Helm CLI
- Single `helmfile.yaml` defines all releases
- Supports environments, secrets, templating

**Example:**
```yaml
# helmfile.yaml
environments:
  dev:
    values:
      - values-dev.yaml
  staging:
    values:
      - values-staging.yaml
  prod:
    values:
      - values-prod.yaml

repositories:
  - name: cnpg
    url: https://cloudnative-pg.github.io/charts
  - name: temporal
    url: https://go.temporal.io/helm-charts

releases:
  - name: postgres
    namespace: scout-infrastructure
    chart: cnpg/cluster
    version: ~0.22.0
    wait: true
    timeout: 600
    values:
      - instances: {{ .Values.postgres.instances }}
      - storage:
          size: {{ .Values.postgres.storageSize }}
    secrets:
      - secrets/postgres-{{ .Environment.Name }}.yaml

  - name: temporal
    namespace: scout-platform
    chart: temporal/temporal
    version: ~1.0.0
    needs: [scout-infrastructure/postgres]
    wait: true
    values:
      - postgresql:
          host: postgres-cnpg-rw.scout-infrastructure.svc.cluster.local

# Deploy with:
# helmfile -e dev sync
# helmfile -e prod sync
```

**When to Use:**
- Need multi-environment support (dev, staging, prod)
- Want declarative deployment without running a server
- Already using Helm, want better orchestration
- CI/CD with push-based deployment

**Pros:**
- No server-side components
- Works with existing Helm charts (no modifications)
- Easy to integrate into CI/CD
- Secrets management via helm-secrets plugin

**Cons:**
- No UI
- Must run manually or via CI/CD (no continuous sync)
- Less sophisticated than ArgoCD/Flux for multi-cluster

#### **ArgoCD**

**What It Is:**
- Kubernetes-native GitOps continuous delivery tool
- Monitors Git repos, syncs to cluster
- Web UI for visualization and management

**Example:**
```yaml
# argocd-apps/scout-platform.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: scout-platform
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/washu-tag/scout
    targetRevision: main
    path: helm/scout-platform
    helm:
      valueFiles:
        - values-prod.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: scout-applications
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
  ignoreDifferences:
    - group: apps
      kind: Deployment
      jsonPointers:
        - /spec/replicas  # Ignore HPA-managed replicas
```

**Sync Waves:**
```yaml
# postgres.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: postgres
  annotations:
    argocd.argoproj.io/sync-wave: "1"  # Deploy first

# temporal.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: temporal
  annotations:
    argocd.argoproj.io/sync-wave: "2"  # Deploy after postgres

# extractor.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: extractor
  annotations:
    argocd.argoproj.io/sync-wave: "3"  # Deploy after temporal
```

**When to Use:**
- Want GitOps workflow (Git as source of truth)
- Need UI for developers to visualize deployments
- Managing multiple clusters from single control plane
- Want automated sync (detect drift, auto-heal)

**Pros:**
- Excellent UI and UX
- Multi-cluster management
- RBAC and multi-tenancy (Projects)
- Health checks and automatic retries
- Active community, very mature

**Cons:**
- Requires server-side installation (ArgoCD in cluster)
- Doesn't fully support Helm hooks (templates them out)
- Pull-based (requires Git commit for changes)
- More complex than Helmfile

#### **Flux CD**

**What It Is:**
- CNCF GitOps tool, Kubernetes-native
- Composed of multiple controllers (source, helm, kustomize, notification)
- No UI (CLI-based)

**Example:**
```yaml
# flux-system/scout-helmrelease.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: scout-charts
  namespace: flux-system
spec:
  interval: 10m
  url: oci://ghcr.io/washu-tag/charts

---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: scout-platform
  namespace: scout-applications
spec:
  interval: 10m
  chart:
    spec:
      chart: scout-platform
      version: ">=2.0.0"
      sourceRef:
        kind: HelmRepository
        name: scout-charts
  values:
    global:
      domain: scout.prod.example.com
  dependsOn:
    - name: postgres
      namespace: scout-infrastructure
    - name: temporal
      namespace: scout-platform
  install:
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
```

**When to Use:**
- Want GitOps with native Helm support (hooks work)
- Prefer Kubernetes-native architecture (CRDs, controllers)
- Don't need UI (CLI is sufficient)
- Want modular architecture (pick which Flux components to use)

**Pros:**
- Native Helm support (hooks work correctly)
- Lightweight (just controllers, no UI)
- Modular (use only what you need)
- CNCF project

**Cons:**
- No UI (CLI only)
- Steeper learning curve (multiple CRDs)
- Less mature than ArgoCD

### 7.3 Recommendation by Use Case

| Use Case | Recommended Tool | Rationale |
|----------|------------------|-----------|
| **Local Development** | Bare Helm | Simplest, fastest iteration |
| **CI/CD (GitHub Actions)** | Helmfile | Push-based deployment, declarative, easy to integrate |
| **Multi-Environment (Dev/Staging/Prod)** | Helmfile or ArgoCD | Environment management, easy to diff |
| **Multi-Cluster (AWS + On-Prem)** | ArgoCD | Best multi-cluster management, UI for visibility |
| **GitOps with Helm Hooks** | Flux | Only GitOps tool with native Helm support |
| **Team with Limited K8s Experience** | ArgoCD | UI reduces learning curve, easier troubleshooting |
| **Platform Team Building IaC** | Flux | Kubernetes-native, modular, scriptable |

### 7.4 Recommended for Scout

**Phase 1 (Migration):** **Helmfile**
- Minimal learning curve (just wraps Helm)
- Easy to integrate into existing CI/CD
- Supports sequential deployment with `needs`
- No server-side components (easy air-gap)
- Can deploy via Ansible initially: `ansible-playbook helmfile-deploy.yaml`

**Phase 2 (Production):** **ArgoCD** (Optional)
- Add GitOps workflow for production environments
- UI helps operations team visualize deployments
- Multi-cluster support for AWS + on-prem
- Keeps Helmfile for local development

**Example Hybrid Approach:**
```yaml
# Local development
helm install scout-platform ./scout-platform -f values-dev.yaml

# CI environment
helmfile -e ci sync

# Production (GitOps)
argocd app create scout-platform \
  --repo https://github.com/washu-tag/scout \
  --path helm/scout-platform \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace scout-applications \
  --values values-prod.yaml \
  --sync-policy automated
```

---

## 8. Implementation Roadmap

### 8.1 Phase 1: Foundation (Months 1-2)

**Goal:** Create umbrella chart that deploys via Ansible (parallel deployment, no breaking changes)

**Tasks:**
1. Create `scout-platform` umbrella chart structure
   ```bash
   helm/
   └── scout-platform/
       ├── Chart.yaml (with all dependencies)
       ├── values.yaml (defaults)
       ├── values-dev.yaml
       ├── values-prod.yaml
       └── charts/ (local subcharts)
   ```

2. Convert external chart deployments to dependencies
   - PostgreSQL (CNPG)
   - MinIO
   - Temporal
   - Trino
   - Superset
   - JupyterHub
   - Prometheus, Loki, Grafana

3. Move in-house charts to subcharts
   - hive-metastore
   - hl7log-extractor
   - hl7-transformer
   - launchpad
   - oauth2-proxy

4. Create Keycloak Helm wrapper chart

5. Add init containers to all services for dependency waiting

6. Create Ansible playbook that deploys umbrella chart
   ```yaml
   - name: Deploy Scout Platform
     hosts: localhost
     tasks:
       - name: Install Scout umbrella chart
         kubernetes.core.helm:
           name: scout-platform
           chart_ref: "{{ scout_repo_dir }}/helm/scout-platform"
           namespace: scout-applications
           values_files:
             - "{{ scout_repo_dir }}/helm/scout-platform/values-{{ environment }}.yaml"
           wait: true
           timeout: 30m
   ```

7. Test deployment on CI environment

**Success Criteria:**
- ✅ Scout deploys successfully via umbrella chart
- ✅ All services healthy and functional
- ✅ Parity with current Ansible deployment

### 8.2 Phase 2: Infrastructure Separation (Months 3-4)

**Goal:** Separate infrastructure provisioning from application deployment

**Tasks:**
1. Create `infra.yaml` playbook
   - K3s installation
   - Storage provisioning (directories + PVs)
   - TLS certificate setup
   - Helm/kubectl installation

2. Create `app.yaml` playbook
   - Just runs `helm install scout-platform`
   - Optional: Deploy via Helmfile

3. Document prerequisites for AWS deployment
   - EKS cluster
   - EBS CSI driver
   - S3 buckets
   - RDS instance
   - IAM roles

4. Create `values-aws.yaml`
   - Disable PostgreSQL, MinIO (use RDS, S3)
   - Configure external endpoints
   - Use EBS storage class

5. Test deployment on AWS EKS

**Success Criteria:**
- ✅ Can deploy Scout on pre-existing K3s cluster (run `app.yaml` without `infra.yaml`)
- ✅ Can deploy Scout on AWS EKS with `values-aws.yaml`

### 8.3 Phase 3: Helmfile Migration (Months 5-6)

**Goal:** Replace Ansible orchestration with Helmfile

**Tasks:**
1. Create `helmfile.yaml` with all releases
   ```yaml
   releases:
     - name: cnpg-operator
       namespace: scout-operators
       chart: cnpg/cloudnative-pg

     - name: postgres
       namespace: scout-infrastructure
       chart: cnpg/cluster
       needs: [scout-operators/cnpg-operator]

     - name: scout-platform
       namespace: scout-applications
       chart: ./helm/scout-platform
       needs:
         - scout-infrastructure/postgres
         - scout-infrastructure/minio
   ```

2. Define environments (dev, staging, prod)
   ```yaml
   environments:
     dev:
       values: [values-dev.yaml]
     prod:
       values: [values-prod.yaml]
   ```

3. Integrate helm-secrets for secret management

4. Update CI/CD to use Helmfile
   ```yaml
   # .github/workflows/deploy.yaml
   - name: Deploy Scout
     run: |
       helmfile -e ${{ matrix.environment }} sync
   ```

5. Test Helmfile deployment on all environments

**Success Criteria:**
- ✅ Can deploy Scout with `helmfile -e prod sync`
- ✅ Dependency ordering works correctly
- ✅ Secrets managed securely

### 8.4 Phase 4: Dynamic Storage (Months 7-8)

**Goal:** Eliminate Ansible storage provisioning, use CSI drivers

**Tasks:**
1. Install Longhorn on on-prem clusters
   ```bash
   helm install longhorn longhorn/longhorn \
     --namespace longhorn-system \
     --create-namespace
   ```

2. Update `values-onprem.yaml` to use Longhorn storage class
   ```yaml
   global:
     storageClass: longhorn
   ```

3. Test deployment without pre-provisioning PVs

4. Document storage options for each environment
   - AWS: EBS CSI
   - On-prem HA: Longhorn
   - On-prem single-node: local-path

5. Create migration guide for existing deployments

**Success Criteria:**
- ✅ Can deploy Scout on fresh K3s cluster without Ansible storage setup
- ✅ Longhorn provides HA storage on on-prem clusters

### 8.5 Phase 5: OCI Distribution (Months 9-10)

**Goal:** Publish Scout as OCI chart for easy distribution

**Tasks:**
1. Package umbrella chart as OCI artifact
   ```bash
   helm package helm/scout-platform
   helm push scout-platform-2.0.0.tgz oci://ghcr.io/washu-tag/charts
   ```

2. Update CI to publish OCI chart on releases
   ```yaml
   # .github/workflows/release.yaml
   - name: Publish Helm Chart
     run: |
       helm push scout-platform-${{ github.ref_name }}.tgz oci://ghcr.io/washu-tag/charts
   ```

3. Document one-command deployment
   ```bash
   helm install scout-platform \
     oci://ghcr.io/washu-tag/charts/scout-platform \
     --version 2.0.0 \
     -f values-aws.yaml \
     -n scout-applications \
     --create-namespace
   ```

4. Test air-gapped deployment with OCI chart in Harbor

**Success Criteria:**
- ✅ Scout chart available as OCI artifact
- ✅ Can deploy from OCI registry with one Helm command

### 8.6 Phase 6: GitOps (Months 11-12) (Optional)

**Goal:** Add ArgoCD for production GitOps workflow

**Tasks:**
1. Install ArgoCD on production cluster
   ```bash
   helm install argocd argo/argo-cd -n argocd --create-namespace
   ```

2. Create ArgoCD Application for Scout
   ```yaml
   apiVersion: argoproj.io/v1alpha1
   kind: Application
   metadata:
     name: scout-platform
   spec:
     source:
       repoURL: https://github.com/washu-tag/scout
       path: helm/scout-platform
       helm:
         valueFiles: [values-prod.yaml]
     destination:
       server: https://kubernetes.default.svc
       namespace: scout-applications
     syncPolicy:
       automated:
         prune: true
         selfHeal: true
   ```

3. Configure sync waves for dependency ordering

4. Set up SSO for ArgoCD UI

5. Train operations team on ArgoCD

**Success Criteria:**
- ✅ ArgoCD syncs Scout from Git automatically
- ✅ Operations team uses UI for deployment visibility
- ✅ Can manage multiple Scout instances (dev, prod) from single ArgoCD

### 8.7 Timeline Summary

| Phase | Duration | Effort | Risk | Dependencies |
|-------|----------|--------|------|--------------|
| 1. Foundation | 2 months | High | Low | None |
| 2. Infra Separation | 2 months | Medium | Medium | Phase 1 |
| 3. Helmfile | 2 months | Low | Low | Phase 2 |
| 4. Dynamic Storage | 2 months | Medium | Medium | Phase 3 |
| 5. OCI Distribution | 2 months | Low | Low | Phase 4 |
| 6. GitOps (Optional) | 2 months | Medium | Low | Phase 5 |

**Total Duration:** 10-12 months (12-14 if including GitOps)

---

## 9. Specific Questions Answered

### 9.1 What are the breaking changes?

**Major Breaking Changes:**

1. **Storage Provisioning** (Biggest Impact)
   - **Current:** Ansible creates directories and PVs with node affinity
   - **Future:** Dynamic provisioning via CSI drivers
   - **Migration:** Must install CSI driver (Longhorn, EBS CSI) or keep Ansible for storage
   - **Impact:** HIGH - Affects all stateful services

2. **Namespace Structure** (Medium Impact)
   - **Current:** 15+ namespaces, services use short names
   - **Future:** 4 namespaces (operators, infrastructure, platform, applications)
   - **Migration:** Update all service references to use FQDNs
   - **Impact:** MEDIUM - Requires configuration changes

3. **Keycloak Deployment** (Medium Impact)
   - **Current:** Raw YAML operator deployment, Ansible configuration tasks
   - **Future:** Helm wrapper chart with post-install Jobs
   - **Migration:** Create Helm chart for operator, convert Ansible tasks to Jobs
   - **Impact:** MEDIUM - Requires chart development

4. **OAuth2-Proxy Namespace** (Low Impact)
   - **Current:** Deployed to `kube-system`
   - **Future:** Deployed to Scout namespace
   - **Migration:** Update ingress annotations/references
   - **Impact:** LOW - Configuration change

5. **Hive Metastore Dual Deployment** (Low Impact)
   - **Current:** Ansible deploys chart twice
   - **Future:** Use Helm dependency aliases
   - **Migration:** Update Chart.yaml dependencies
   - **Impact:** LOW - Chart structure change

6. **Air-Gapped Deployment** (Low Impact)
   - **Current:** Ansible configures K3s registry mirrors
   - **Future:** Keep registry mirrors in Layer 1, distribute chart as OCI
   - **Migration:** No change to registry mirrors, package chart as OCI
   - **Impact:** LOW - Process change, not technical

**Seamless Migrations:**

✅ External Helm charts (PostgreSQL, MinIO, Temporal, etc.) - Just change from Ansible to Chart.yaml dependencies
✅ In-house charts (launchpad, extractors) - Just move to subcharts directory
✅ Service configuration - Values files replace Ansible templates (1:1 mapping)
✅ Sequencing - Use Helmfile `needs` or init containers (same result, different mechanism)

### 9.2 What parts should stay in Ansible and aren't portable at all?

**Must Stay in Ansible (Platform-Specific):**

1. **K3s Installation**
   - Installing K3s binary
   - Configuring systemd service
   - Setting up kubeconfig
   - **AWS Alternative:** Terraform for EKS

2. **Registry Mirrors (Air-Gapped)**
   - Creating `/etc/rancher/k3s/registries.yaml`
   - Installing Harbor on staging node
   - **AWS Alternative:** ECR with VPC endpoints

3. **TLS Certificate Management**
   - Reading certs from filesystem
   - Creating K8s secrets from files
   - **AWS Alternative:** ACM + AWS Load Balancer Controller
   - **Portable Alternative:** cert-manager for Let's Encrypt

4. **System Package Installation**
   - `python3-kubernetes` for Ansible modules
   - SELinux packages (air-gapped)
   - **Not needed if using Helmfile/ArgoCD instead of Ansible**

**Can Move Out of Ansible:**

✅ All Helm chart deployments
✅ Kubernetes resource creation (StorageClasses, Secrets from templates)
✅ Service configuration (move to Helm values)
✅ Keycloak configuration (move to Helm post-install Jobs)

**Recommended Split:**

```
ansible/
├── playbooks/
│   ├── infra/
│   │   ├── k3s.yaml (stays)
│   │   ├── storage-provisioning.yaml (stays for now, phase out with CSI)
│   │   ├── tls.yaml (stays or use cert-manager)
│   │   └── harbor.yaml (stays for air-gap)
│   └── app/
│       └── helmfile-deploy.yaml (temporary, replace with helmfile directly)

helm/
└── scout-platform/ (umbrella chart)
    └── (all application deployment)

helmfile.yaml (eventual replacement for Ansible app deployment)
```

### 9.3 How to enforce sequence in Helm chart? Are there ways besides hooks? Would multiple helm charts solve this?

**Option 1: Helm Hooks** (Within Single Chart)
```yaml
# templates/wait-for-postgres-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-5"
```
- **Pros:** Native Helm, works everywhere
- **Cons:** Must create Jobs for each dependency

**Option 2: Init Containers** (Application-Level)
```yaml
# templates/deployment.yaml
spec:
  initContainers:
    - name: wait-postgres
      image: postgres:16
      command: [sh, -c, "until pg_isready -h $DB_HOST; do sleep 5; done"]
```
- **Pros:** Standard Kubernetes, retries automatic
- **Cons:** Each app handles own dependencies

**Option 3: Application Retry Logic** (Recommended)
```yaml
# Spring Boot automatically retries database connections
spring.datasource.hikari.initialization-fail-timeout: -1
```
- **Pros:** No K8s-specific code, handles runtime failures
- **Cons:** Pod shows as Running but may not be functional

**Option 4: Helmfile `needs`** (Multi-Chart Orchestration)
```yaml
releases:
  - name: postgres
    chart: cnpg/cluster

  - name: extractor
    chart: ./scout/extractor
    needs: [postgres]  # Waits for postgres to be ready
```
- **Pros:** Explicit ordering, clean separation
- **Cons:** Requires Helmfile (additional tool)

**Option 5: ArgoCD Sync Waves** (GitOps Orchestration)
```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "1"  # Deploy postgres in wave 1
---
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "2"  # Deploy extractor in wave 2
```
- **Pros:** Visual in UI, health checks between waves
- **Cons:** Requires ArgoCD

**Recommendation: Multi-Layered Approach**

1. **Use init containers + application retry** for robustness (works without orchestration)
2. **Add Helmfile `needs`** for faster convergence (production deployments)
3. **Consider ArgoCD sync waves** for GitOps workflows (optional)

**Multiple Helm Charts:**

Yes, deploying as multiple charts solves sequencing:

```yaml
# Option A: 4 separate charts deployed via Helmfile
releases:
  - name: scout-operators
    chart: ./charts/scout-operators

  - name: scout-infrastructure
    chart: ./charts/scout-infrastructure
    needs: [scout-operators]

  - name: scout-platform
    chart: ./charts/scout-platform
    needs: [scout-infrastructure]

  - name: scout-applications
    chart: ./charts/scout-applications
    needs: [scout-platform]
```

**Pros:**
- Clear dependency boundaries
- Can deploy/upgrade each layer independently
- Matches namespace strategy (4 charts → 4 namespace groups)

**Cons:**
- More complex than single umbrella chart
- Must use orchestration tool (Helmfile or ArgoCD)

**Recommendation:** Start with **single umbrella chart** + init containers for simplicity, migrate to **4-chart architecture** + Helmfile if deployment time becomes issue.

---

## 10. Recommended Next Steps

### 10.1 Immediate Actions (Next 2 Weeks)

1. **Review and Discuss This Document**
   - Share with team
   - Identify any concerns or missing requirements
   - Get buy-in on recommended architecture

2. **Proof of Concept**
   - Create minimal umbrella chart with 2-3 services
   - Test deployment on local K3s cluster
   - Validate dependency ordering with init containers
   - Estimated effort: 1 week

3. **Estimate Effort**
   - Assign owners to each phase
   - Estimate developer time per phase
   - Identify blockers (e.g., Keycloak Helm chart development)

### 10.2 Decision Points

**Must Decide:**

1. **Namespace Strategy:** Single namespace, 4 namespaces, or keep 15+?
   - **Recommendation:** 4 namespaces (operators, infrastructure, platform, applications)

2. **Storage Strategy:** Dynamic provisioning (Longhorn) or keep Ansible?
   - **Recommendation:** Longhorn for on-prem, EBS for AWS (Phase 4)

3. **Orchestration Tool:** Helmfile, ArgoCD, or bare Helm?
   - **Recommendation:** Helmfile (Phase 3), optionally add ArgoCD later (Phase 6)

4. **Migration Strategy:** Big-bang or phased?
   - **Recommendation:** Phased (6-phase roadmap over 12 months)

5. **AWS Support:** Must-have for 1.0 or future enhancement?
   - **Recommendation:** Must-have (Phase 2), validates portability

### 10.3 Success Metrics

**Phase 1 Success:**
- Scout deploys via single `helm install` command
- All services functional and pass integration tests

**Phase 2 Success:**
- Scout deploys on AWS EKS with `values-aws.yaml`
- Scout deploys on fresh K3s cluster without full Ansible

**Phase 4 Success:**
- Zero Ansible required for application deployment
- Storage auto-provisioned via CSI drivers

**Phase 5 Success:**
- Scout available as public OCI chart
- External users can deploy Scout with one Helm command

---

## 11. Conclusion

Refactoring Scout from Ansible to a portable Helm architecture is **highly feasible** and **strongly recommended**. The proposed three-layer architecture (Infrastructure → Umbrella Chart → Orchestration) provides clear separation of concerns while maintaining the flexibility to deploy on any Kubernetes platform.

**Key Takeaways:**

1. ✅ **Most components migrate seamlessly** - External charts and in-house charts move to umbrella dependencies with minimal changes

2. ⚠️ **Storage provisioning is the main blocker** - Solved with dynamic provisioning (Longhorn, EBS CSI) or keeping Ansible for PV creation during migration

3. ✅ **Dependency ordering is solved** - Multiple proven patterns (init containers, Helm hooks, Helmfile, ArgoCD sync waves)

4. ✅ **Namespace consolidation is beneficial** - Recommend 4 namespaces instead of 15+ for better portability

5. ✅ **Helmfile is the right orchestration tool** - Low barrier to entry, works with existing Helm charts, no server required

6. ✅ **Phased migration minimizes risk** - 6 phases over 12 months allows for incremental validation

**Expected Outcome:**

By the end of this refactoring, Scout will be deployable on any Kubernetes cluster (EKS, GKE, AKS, K3s, OpenShift) with a single command:

```bash
helm install scout-platform oci://ghcr.io/washu-tag/charts/scout-platform \
  --version 2.0.0 \
  -f values-aws.yaml \  # or values-onprem.yaml, values-azure.yaml, etc.
  -n scout-applications \
  --create-namespace
```

This dramatically reduces deployment complexity and enables Scout to reach new deployment environments with minimal effort.

---

## Appendices

### Appendix A: Helm Umbrella Chart Example

See proposed structure in [Section 2.1](#21-three-layer-architecture).

### Appendix B: Helmfile Complete Example

See example in [Section 7.2](#72-tool-deep-dive).

### Appendix C: Storage Comparison Matrix

See table in [Section 5.2](#52-portability-patterns).

### Appendix D: Glossary

- **Umbrella Chart:** A Helm chart that primarily consists of dependencies on other charts
- **CSI Driver:** Container Storage Interface driver for dynamic storage provisioning
- **GitOps:** Declarative deployment where Git is the source of truth
- **Sync Wave:** ArgoCD concept for phased deployment
- **Helmfile:** Declarative tool for orchestrating multiple Helm releases
- **OCI Chart:** Helm chart packaged and distributed as OCI (Docker-compatible) artifact
- **PV:** PersistentVolume - Kubernetes resource representing storage
- **PVC:** PersistentVolumeClaim - Request for storage by a pod

### Appendix E: References

- [Helm Best Practices - Dependencies](https://helm.sh/docs/chart_best_practices/dependencies/)
- [Helm Hooks Documentation](https://helm.sh/docs/topics/charts_hooks/)
- [Helmfile Documentation](https://helmfile.readthedocs.io/)
- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Flux CD Documentation](https://fluxcd.io/docs/)
- [AWS EKS Hybrid Nodes](https://docs.aws.amazon.com/eks/latest/userguide/hybrid-nodes-overview.html)
- [Longhorn Documentation](https://longhorn.io/docs/)
- [Scout Current Documentation](https://washu-scout.readthedocs.io/)

---

**Document Version:** 1.0
**Last Updated:** November 2025
**Next Review:** After Phase 1 Completion (Month 3)

---

## 12. Update: Storage Provisioning Implementation (December 2025)

**Status:** The storage provisioning recommendations from this document have been **fully implemented** as of commit `ae194ae2` (November 10, 2025). This section documents what was implemented and how it impacts the overall refactoring feasibility.

### 12.1 What Was Implemented

The codebase has migrated from static PV provisioning to dynamic volume provisioning, implementing **"Option A: Dynamic Provisioning"** from [Section 5.2](#52-portability-patterns) with significant enhancements beyond the original recommendation.

#### Code Deletion (Breaking Change)

In a single commit, **~220 lines of static PV provisioning code were removed**:

**Deleted Files:**
- `ansible/roles/scout_common/tasks/storage_setup.yaml` (entire file)
- `ansible/roles/scout_common/tasks/storage_dir.yaml` (entire file)

**Deleted Tasks from Playbooks:**
- Directory creation plays from: `jupyter.yaml`, `lake.yaml`, `monitor.yaml`, `orchestrator.yaml`, `orthanc.yaml`, `postgres.yaml`
- Two-play structure eliminated (previously: Play 1 creates directories, Play 2 deploys services)

**Deleted Tasks from Roles:**
- `storage.yaml` tasks removed from: postgres, minio, cassandra, elasticsearch, jupyter, prometheus, loki, grafana, orthanc, dcm4chee roles
- All manual PersistentVolume creation tasks
- All directory creation and permission-setting tasks

#### Storage Class Configuration System

**Per-Service Variables** (`ansible/group_vars/all.yaml`):

```yaml
# Default: empty string for all services
postgres_storage_class: ''
temporal_storage_class: ''
cassandra_storage_class: ''
elasticsearch_storage_class: ''
minio_storage_class: ''
jupyter_storage_class: ''
prometheus_storage_class: ''
loki_storage_class: ''
grafana_storage_class: ''
orthanc_storage_class: ''
dcm4chee_storage_class: ''

# Optional multi-disk configuration
storage_classes_to_create: []
```

**IMPORTANT: Omit vs Empty String**

The implementation uses Jinja2's `omit` filter to **completely omit** the `storageClassName` field when the variable is empty:

```yaml
# In Helm values templates (e.g., postgres/tasks/deploy.yaml):
storage:
  storageClass: '{{ postgres_storage_class if postgres_storage_class else omit }}'
  size: '{{ postgres_storage_size }}'
```

**Behavior:**
- `postgres_storage_class: ''` → Field omitted from Helm values → Kubernetes uses cluster default StorageClass
- `postgres_storage_class: 'gp3'` → `storageClass: gp3` passed to Helm → Kubernetes uses specified StorageClass

**Why Not `storageClass: ""`?**

Setting `storageClassName: ""` (empty string) is **not the same** as omitting the field:
- **Omitted field**: Kubernetes uses the cluster's default StorageClass
- **Empty string (`""`)**: Kubernetes interprets this as explicitly requesting **no** StorageClass, which prevents dynamic provisioning and requires a manually-created PV with no `storageClassName`

The implementation correctly uses `omit` to get default-StorageClass behavior.

#### Implementation in Service Roles

**PostgreSQL** (`ansible/roles/postgres/tasks/deploy.yaml`):
```yaml
cluster:
  instances: {{ postgres_instances }}
  storage:
    storageClass: '{{ postgres_storage_class if postgres_storage_class else omit }}'
    size: '{{ postgres_storage_size }}'
```

**Cassandra** (`ansible/roles/cassandra/tasks/deploy.yaml`):
```yaml
cassandraDataVolumeClaimSpec:
  storageClassName: '{{ cassandra_storage_class if cassandra_storage_class else omit }}'
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: '{{ cassandra_storage_size }}'
```

**MinIO** (`ansible/roles/minio/tasks/deploy.yaml`):
```yaml
pools:
  - servers: {{ minio_servers }}
    volumesPerServer: {{ minio_volumes_per_server }}
    size: {{ minio_storage_size }}
    storageClassName: '{{ minio_storage_class if minio_storage_class else omit }}'
```

**Same pattern applied to:** Elasticsearch, Prometheus, Loki, Grafana, JupyterHub, Temporal, Orthanc, DCM4chee

#### Multi-Disk Storage Classes (Optional Feature)

**New Task:** `ansible/roles/scout_common/tasks/storage_classes.yaml`

For on-premise deployments requiring I/O isolation across multiple disks, this task configures k3s's local-path-provisioner with multiple storage classes:

```yaml
- name: Configure custom storage classes for on-premise multi-disk deployments
  when: storage_classes_to_create | length > 0
  block:
    - name: Update local-path-provisioner ConfigMap
      kubernetes.core.k8s:
        state: present
        definition:
          apiVersion: v1
          kind: ConfigMap
          metadata:
            name: local-path-config
            namespace: local-path-storage
          data:
            config.json: |
              {
                "nodePathMap": [],
                "storageClassConfigs": {
                  {% for sc in storage_classes_to_create %}
                  "{{ sc.name }}": {
                    "nodePathMap": [
                      {
                        "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES",
                        "paths": ["{{ sc.path }}"]
                      }
                    ]
                  }{{ "," if not loop.last else "" }}
                  {% endfor %}
                }
              }

    - name: Create StorageClass resources
      kubernetes.core.k8s:
        state: present
        definition:
          apiVersion: storage.k8s.io/v1
          kind: StorageClass
          metadata:
            name: '{{ item.name }}'
          provisioner: rancher.io/local-path
          volumeBindingMode: WaitForFirstConsumer
          reclaimPolicy: Delete
      loop: '{{ storage_classes_to_create }}'
```

**Integration:** Called early in `ansible/playbooks/main.yaml` before any services are deployed.

**How It Works:**
1. Configure local-path-provisioner's `storageClassConfigs` feature
2. Create StorageClass resources that point to different disk paths
3. Services assign themselves to storage classes via variables
4. Provisioning remains fully dynamic - no manual PV creation

#### Special Case: Jupyter ReadWriteMany (RWX)

**Challenge:** Jupyter notebooks require ReadWriteMany (RWX) storage to support multi-node GPU scheduling. Most CSI drivers don't support RWX (EBS, local-path, etc.).

**Solution** (`ansible/roles/jupyter/tasks/deploy.yaml`):

```yaml
# Optional: Create static PV with network-mounted directory (on-prem NFS/CIFS)
- name: Create static PV for Jupyter notebook storage (on-prem)
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: v1
      kind: PersistentVolume
      metadata:
        name: '{{ jupyter_singleuser_pv_name }}'
      spec:
        capacity:
          storage: '{{ jupyter_singleuser_storage_size }}'
        accessModes:
          - ReadWriteMany
        persistentVolumeReclaimPolicy: Retain
        storageClassName: ''
        hostPath:
          path: '{{ jupyter_singleuser_hostpath }}'
          type: DirectoryOrCreate
  when: jupyter_singleuser_hostpath | length > 0

# Create PVC (binds to static PV on-prem, or cloud RWX StorageClass)
- name: Create PersistentVolumeClaim for Jupyter single-user storage
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: v1
      kind: PersistentVolumeClaim
      metadata:
        name: '{{ jupyter_singleuser_pvc }}'
        namespace: '{{ jupyter_namespace }}'
      spec:
        accessModes:
          - ReadWriteMany
        resources:
          requests:
            storage: '{{ jupyter_singleuser_storage_size }}'
        storageClassName: '{{ jupyter_singleuser_storage_class if jupyter_singleuser_storage_class else omit }}'
        volumeName: '{{ jupyter_singleuser_pv_name if jupyter_singleuser_hostpath else omit }}'
```

**Two Configuration Modes:**

1. **On-Premise with Network Mount:**
   ```yaml
   jupyter_singleuser_hostpath: '/mnt/nfs/jupyter-notebooks'  # Network-mounted directory
   jupyter_singleuser_storage_class: ''  # Use static PV
   ```
   Creates static PV pointing to network mount, PVC binds to it.

2. **Cloud with RWX StorageClass:**
   ```yaml
   jupyter_singleuser_hostpath: ''  # No static PV
   jupyter_singleuser_storage_class: 'efs-sc'  # AWS EFS CSI driver
   ```
   Uses dynamic provisioning with RWX-capable StorageClass.

**Dual Variables:**
- `jupyterhub_storage_class`: For JupyterHub database (RWO sufficient)
- `jupyter_singleuser_storage_class`: For notebook storage (RWX required)

### 12.2 Configuration Examples

#### Default Configuration (Cloud or Single-Disk On-Prem)

```yaml
# inventory.yaml
k3s_cluster:
  vars:
    # All storage classes empty - cluster default will be used
    postgres_storage_class: ""
    cassandra_storage_class: ""
    minio_storage_class: ""
    # ... all other services also ""

    # No custom storage classes
    storage_classes_to_create: []
```

**Result:** All services use cluster default StorageClass:
- **k3s:** `local-path` (built-in)
- **AWS EKS:** `gp3` or `gp2` (EBS CSI driver)
- **GCP GKE:** `standard-rwo` (GCE PD CSI driver)
- **Azure AKS:** `managed-premium` (Azure Disk CSI driver)

#### Multi-Disk On-Prem Configuration

```yaml
# inventory.yaml
k3s_cluster:
  vars:
    # Define custom storage classes pointing to different disks
    storage_classes_to_create:
      - name: "local-database"
        path: "/mnt/nvme0/k3s-storage"      # Fast NVMe for databases
      - name: "local-objectstorage"
        path: "/mnt/hdd0/k3s-storage"       # Large HDD for object storage
      - name: "local-monitoring"
        path: "/mnt/nvme1/k3s-storage"      # Separate NVMe for metrics

    # Assign services to storage classes for I/O isolation
    postgres_storage_class: "local-database"
    cassandra_storage_class: "local-database"
    temporal_storage_class: "local-database"

    minio_storage_class: "local-objectstorage"

    prometheus_storage_class: "local-monitoring"
    loki_storage_class: "local-monitoring"

    # Other services use default
    elasticsearch_storage_class: ""
    grafana_storage_class: ""
```

**Result:**
1. Ansible configures local-path-provisioner with 3 storage classes
2. When PostgreSQL creates PVC, it requests `local-database` StorageClass
3. local-path-provisioner creates PV on `/mnt/nvme0/k3s-storage`
4. No manual directory creation or PV provisioning needed

#### AWS EKS Configuration

```yaml
# inventory.yaml - Not actually used for EKS deployment
# This shows the values that would be set if using Ansible to deploy to EKS

eks_cluster:
  vars:
    # Use AWS EBS gp3 volumes
    postgres_storage_class: "gp3"
    cassandra_storage_class: "gp3"
    minio_storage_class: "gp3"
    # ... all other services use gp3

    # No custom storage classes (use EBS CSI driver)
    storage_classes_to_create: []
```

**Result:** All services use AWS EBS CSI driver with gp3 volumes (or omit to use cluster default).

### 12.3 Documentation Added

Three documents were created to explain the new storage approach:

1. **ADR 0004** (`docs/internal/adr/0004-storage-provisioning-approach.md`)
   - Decision record explaining why dynamic provisioning was chosen
   - Rationale for each platform (k3s, AWS, GCP, Azure)
   - Trade-offs and alternatives considered
   - Multi-disk configuration justification

2. **Inventory Documentation Update** (`docs/source/technical/inventory.md`)
   - Practical configuration examples
   - When to use each storage class pattern
   - Platform-specific defaults
   - Troubleshooting guidance

3. **Jupyter Implementation Plan** (`docs/internal/jupyterhub-storage-implementation-plan.md`)
   - Detailed explanation of RWX requirements
   - Step-by-step implementation for Jupyter storage
   - Network mount configuration for on-premise
   - Cloud RWX StorageClass setup (EFS, Filestore, Azure Files)

### 12.4 Impact on Helm Refactoring Feasibility

The storage provisioning implementation **dramatically improves** the feasibility of the Helm refactoring outlined in this document. Here's the updated assessment:

#### Breaking Changes: Updated Status

From [Section 3.2](#32-breaking-changes-required), here's the updated status of each breaking change:

| Breaking Change | Original Impact | Current Status | Notes |
|----------------|-----------------|----------------|-------|
| **1. Storage Provisioning** | ❌ HIGH | ✅ **RESOLVED** | Fully dynamic, no Ansible required |
| **2. Keycloak Deployment** | ❌ MEDIUM | ⚠️ **UNCHANGED** | Still raw YAML + Ansible configuration |
| **3. OAuth2-Proxy Deployment** | ❌ LOW | ⚠️ **UNCHANGED** | Still deployed to `kube-system` |
| **4. Air-Gapped Deployment** | ❌ LOW | ⚠️ **UNCHANGED** | Still Ansible-based registry config |
| **5. Multi-Instance Hive Metastore** | ❌ LOW | ⚠️ **UNCHANGED** | Still deployed twice via Ansible |

**Current Multi-Namespace Architecture:** ⚠️ **UNCHANGED** - Still using 15+ namespaces

#### Implementation Roadmap: Accelerated Timeline

**Original Timeline** (from [Section 8.7](#87-timeline-summary)): 10-12 months

**Updated Timeline:** **6-8 months** (40% reduction)

| Phase | Original Duration | Updated Duration | Reduction | Reason |
|-------|-------------------|------------------|-----------|--------|
| 1. Foundation | 2 months | **4-6 weeks** | 25% faster | No storage provisioning to handle in umbrella chart |
| 2. Infra Separation | 2 months | **3-4 weeks** | 50% faster | Storage already separated, just need TLS/registry |
| 3. Helmfile | 2 months | 2 months | No change | Not affected by storage |
| 4. Dynamic Storage | 2 months | **0 months** | **Eliminated** | Already implemented |
| 5. OCI Distribution | 2 months | 2 months | No change | Not affected by storage |
| 6. GitOps (Optional) | 2 months | 2 months | No change | Not affected by storage |
| **Total (without GitOps)** | **10 months** | **6 months** | **40% faster** | |
| **Total (with GitOps)** | **12 months** | **8 months** | **33% faster** | |

#### Phase 1: Foundation - What's Now Easier

**Original Tasks from [Section 8.1](#81-phase-1-foundation-months-1-2):**

~~1. Handle static PV provisioning in umbrella chart~~ **ELIMINATED**
~~2. Create storage abstraction layer for cloud vs on-prem~~ **ELIMINATED**
~~3. Design PV/PVC template patterns~~ **ELIMINATED**
~~4. Test storage provisioning on multiple platforms~~ **ELIMINATED**

**Remaining Tasks:**
1. ✅ Create `scout-platform/Chart.yaml` with all dependencies
2. ✅ Move in-house charts to `subcharts/` directory
3. ✅ Create Keycloak Helm wrapper (moderate effort)
4. ✅ Add init containers for dependency waiting
5. ✅ Create values files with storage class variables

**Simplified Umbrella Chart Structure:**

```yaml
# scout-platform/values.yaml (simplified)
global:
  # Storage configuration - just pass through to subcharts
  storage:
    class: ""  # Empty = omit field, use cluster default

    # Optional: Multi-disk on-prem (advanced)
    multiDisk:
      enabled: false
      classes: []
      # Example:
      # enabled: true
      # classes:
      #   - name: local-database
      #     path: /mnt/disk1/k3s-storage
      #   - name: local-objectstorage
      #     path: /mnt/disk2/k3s-storage

postgresql:
  enabled: true
  cluster:
    storage:
      # Inherits from global.storage.class or override here
      storageClass: '{{ .Values.global.storage.class | default "" }}'
      size: 100Gi

minio:
  enabled: true
  tenant:
    pools:
      - servers: 4
        volumesPerServer: 2
        # Inherits from global.storage.class or override here
        storageClassName: '{{ .Values.global.storage.class | default "" }}'
        size: 750Gi
```

**IMPORTANT Implementation Note:**

In Helm templates, you must use the `omit` function (requires Helm 3.12+) or conditional template logic to omit the field when empty:

```yaml
# Option 1: Using omit (Helm 3.12+)
{{- with .Values.global.storage.class }}
storageClass: {{ . }}
{{- end }}

# Option 2: Using conditional (any Helm version)
{{- if ne .Values.global.storage.class "" }}
storageClass: {{ .Values.global.storage.class }}
{{- end }}
```

**NOT THIS** (this sets storageClassName to empty string, which disables dynamic provisioning):
```yaml
# WRONG - Do not do this
storageClass: {{ .Values.global.storage.class | default "" }}
```

#### Phase 4: Dynamic Storage - Now Complete

**Original Phase 4 Tasks from [Section 8.4](#84-phase-4-dynamic-storage-months-7-8):**

~~1. Install Longhorn on on-prem clusters~~ **NOT NEEDED** - local-path-provisioner sufficient
~~2. Update `values-onprem.yaml` to use dynamic provisioning~~ **COMPLETE**
~~3. Test deployment without pre-provisioning PVs~~ **COMPLETE**
~~4. Document storage options for each environment~~ **COMPLETE** (ADR 0004, inventory docs)
~~5. Create migration guide for existing deployments~~ **NOT NEEDED** - clean break, no migration

**Phase 4 is now complete.** No work remains for this phase.

#### Updated Effort Estimate

**Work Remaining for Full Helm Refactor:**

| Component | Original Estimate | Updated Estimate | Notes |
|-----------|-------------------|------------------|-------|
| **Storage Layer** | 2 months | **0 months** | ✅ Complete |
| **Umbrella Chart Creation** | 2 months | **1 month** | Significantly simpler without storage |
| **Keycloak Helm Wrapper** | 1 month | 1 month | No change (still moderate effort) |
| **Namespace Refactoring** | 1 month | 1 month | No change (still optional) |
| **Helmfile Integration** | 2 months | 2 months | No change |
| **Testing & Documentation** | 2 months | 1 month | Less to test without storage layer |
| **Total (Core Refactor)** | **10 months** | **6 months** | **40% reduction** |

### 12.5 Recommendations for Helm Refactor

Given the current state of storage implementation, here are updated recommendations:

#### 1. Start Phase 1 Immediately

**Rationale:** The biggest blocker (storage) is resolved. The remaining work is mostly mechanical chart organization.

**Quick Wins:**
- All Helm charts can use standard PVC templates
- No complex storage abstraction layer needed
- Cloud deployments trivial (just set `storageClass` in values)
- Can reuse existing `storage_classes_to_create` pattern for advanced on-prem

#### 2. Leverage Existing Storage Variables

**Current Ansible Variables:**
```yaml
postgres_storage_class: ""
minio_storage_class: ""
# ... etc
```

**Map Directly to Helm Values:**
```yaml
# values.yaml
postgresql:
  cluster:
    storage:
      storageClass: ""  # Maps from postgres_storage_class

minio:
  tenant:
    pools:
      - storageClassName: ""  # Maps from minio_storage_class
```

**Migration Path:** Keep Ansible variables, template them into Helm values files initially, then transition to pure Helm values.

#### 3. Preserve Multi-Disk Feature

The `storage_classes_to_create` feature is valuable for advanced on-premise deployments. Include it in the umbrella chart:

```yaml
# values.yaml
global:
  storage:
    multiDisk:
      enabled: false
      classes: []

# values-onprem-multidisk.yaml
global:
  storage:
    multiDisk:
      enabled: true
      classes:
        - name: local-database
          path: /mnt/disk1/k3s-storage
        - name: local-objectstorage
          path: /mnt/disk2/k3s-storage
```

**Implementation:** Create Helm pre-install hook Job that configures local-path-provisioner ConfigMap (equivalent to current `storage_classes.yaml` task).

#### 4. Handle Jupyter RWX Carefully

The Jupyter RWX solution is elegant and should be preserved:

```yaml
# values-onprem.yaml (with NFS mount)
jupyter:
  singleuser:
    storage:
      type: static  # Use static PV with hostPath
      hostPath: /mnt/nfs/jupyter-notebooks
      storageClass: ""
      capacity: 250Gi

# values-aws.yaml (with EFS)
jupyter:
  singleuser:
    storage:
      type: dynamic  # Use EFS CSI driver
      hostPath: ""
      storageClass: efs-sc
      capacity: 1Ti
```

**Helm Template Logic:**
```yaml
{{- if .Values.jupyter.singleuser.storage.type eq "static" }}
# Create static PV with hostPath
{{- else }}
# Rely on dynamic provisioning
{{- end }}
```

#### 5. Skip "Option B" Migration Path

**Original Recommendation:** Use "Option B: Pre-Provisioning with Ansible" as interim step.

**Updated Recommendation:** **Skip Option B entirely.** The implementation already did a clean break to full dynamic provisioning. Maintain this in the Helm refactor.

**No Backward Compatibility Needed:** Don't support migration from static PVs. Require clean deployment.

#### 6. Document Storage Class Behavior

**Critical Documentation Point:** Explain the difference between omitting `storageClass` vs setting it to empty string.

**Include in Helm Chart README:**

```markdown
## Storage Configuration

Scout uses dynamic volume provisioning by default. Storage behavior is controlled by the `storageClass` field:

### Kubernetes StorageClass Behavior

- **Field omitted** (default): Uses cluster's default StorageClass
  ```yaml
  postgresql:
    cluster:
      storage:
        # storageClass not specified
        size: 100Gi
  ```

- **Field set to specific class**: Uses that StorageClass
  ```yaml
  postgresql:
    cluster:
      storage:
        storageClass: "gp3"
        size: 100Gi
  ```

- **Field set to empty string `""`**: Disables dynamic provisioning, requires manual PV
  ```yaml
  # DO NOT DO THIS unless you have a specific reason
  postgresql:
    cluster:
      storage:
        storageClass: ""  # Disables dynamic provisioning!
        size: 100Gi
  ```

### Platform Defaults

When `storageClass` is omitted, Kubernetes uses the cluster's default:

- **k3s**: `local-path` (hostPath on node)
- **AWS EKS**: `gp3` or `gp2` (EBS volumes)
- **GCP GKE**: `standard-rwo` (GCE Persistent Disk)
- **Azure AKS**: `managed-premium` (Azure Disk)
```

### 12.6 Remaining Challenges

While storage is resolved, these challenges remain for the Helm refactor:

#### 1. Keycloak Deployment (Medium Effort)

**Current:** Raw YAML manifests + Ansible tasks for configuration
**Required:** Helm wrapper chart with post-install Jobs

**Estimated Effort:** 2-3 weeks

**Approach:**
- Create Helm chart that wraps Keycloak Operator YAML
- Convert Ansible configuration tasks to Kubernetes Jobs using `keycloak-config-cli`
- Use Helm hooks for proper ordering

#### 2. Namespace Consolidation (Optional)

**Current:** 15+ namespaces
**Recommended:** 4 namespaces (operators, infrastructure, platform, applications)

**Estimated Effort:** 1-2 weeks (if pursued)

**Decision Point:** This is optional. The umbrella chart can work with current namespace structure if desired. Consolidation offers cleaner architecture but adds migration complexity.

#### 3. OAuth2-Proxy Relocation (Low Effort)

**Current:** Deployed to `kube-system`
**Recommended:** Deploy to Scout namespace

**Estimated Effort:** 1 week

**Impact:** Must update ingress annotations/middleware references

#### 4. Dependency Ordering (Medium Effort)

**Required:** Implement init containers + application retry logic

**Estimated Effort:** 2 weeks

**Approach from [Section 4.8](#48-recommended-approach):**
1. Add init containers to all services (wait for dependencies)
2. Configure application-level retry (Spring Boot, Temporal, etc.)
3. Optional: Add Helmfile `needs` for faster convergence

### 12.7 Conclusion

The storage provisioning implementation represents **40% of the original Helm refactoring effort**. With this complete, the remaining work is significantly more straightforward and lower risk.

**Key Takeaways:**

1. ✅ **Storage blocker eliminated** - Biggest technical challenge resolved
2. ✅ **Cloud portability achieved** - Already works on k3s, AWS, GCP, Azure
3. ✅ **Advanced features preserved** - Multi-disk support maintained
4. ✅ **Clean architecture** - No legacy PV provisioning to support
5. ⚠️ **Remaining work is chart organization** - Mechanical but time-consuming

**Updated Feasibility Assessment:**

| Original Assessment | Updated Assessment |
|---------------------|-------------------|
| ✅ Feasible with phased approach | ✅ **Highly feasible with accelerated timeline** |
| ⚠️ Storage is main blocker | ✅ **Storage blocker removed** |
| 🎯 10-12 month timeline | 🎯 **6-8 month timeline** |
| ⚠️ Breaking changes in storage | ✅ **Breaking changes already implemented** |

**Recommendation:** **Proceed with Helm refactoring.** The foundation is in place, risk is significantly reduced, and timeline is much shorter than originally estimated.

---

**Update Version:** 1.1
**Update Date:** December 2025
**Updated By:** Analysis of commit ae194ae2 and related storage implementation
**Next Review:** After Phase 1 umbrella chart implementation begins
