# Staging Node Architecture

> **Note:** For the architectural decision to separate the staging node (Harbor registry) from the Ansible control node ("jump node"), see [ADR-0007: Jump Node Architecture](adr/0007-jump-node-architecture-adr.md). This document covers operational details of the staging node and Harbor configuration.

## Overview

The staging node serves as a **data diode** for air-gapped Scout deployments. It hosts a Harbor container registry that proxies and caches container images from public registries (Docker Hub, GitHub Container Registry) so that the main k3s cluster nodes can operate without direct internet access.

## Problem Statement

In air-gapped or restricted network environments:

1. **Security requirements** prevent k3s cluster nodes from accessing the internet directly
2. **Container images** are required from Docker Hub, GHCR, and other public registries
3. **Helm charts** must be pulled from public repositories to deploy services
4. **Manual image management** (downloading and importing images) is error-prone and labor-intensive

The staging node solves problems #1 and #2. For #3, Helm installations are run from the Ansible control node ("jump node"), which has internet access and uses the Kubernetes remote API via a fetched kubeconfig to deploy to the air-gapped cluster. See [ADR-0007](adr/0007-jump-node-architecture-adr.md) for details on the separation of staging and jump node roles.

## Architecture

### High-Level Design

```
                         Internet
                            │
                            │
                ┌───────────▼────────────┐
                │  Staging Node          │
                │  ┌──────────────────┐  │
                │  │ K3s (standalone) │  │
                │  │  ┌────────────┐  │  │
                │  │  │  Harbor    │  │  │
                │  │  │  Registry  │  │  │
                │  │  └────────────┘  │  │
                │  └──────────────────┘  │
                └───────────┬────────────┘
                            │ HTTPS (self-signed)
                            │
                ┌───────────▼────────────┐
                │  Air-Gapped Network    │
                │                        │
                │  ┌──────────────────┐  │
                │  │ K3s Cluster      │  │
                │  │  ┌────────────┐  │  │
                │  │  │ Control    │  │  │
                │  │  │ Node       │  │  │
                │  │  └────────────┘  │  │
                │  │  ┌────────────┐  │  │
                │  │  │ Worker     │  │  │
                │  │  │ Nodes      │  │  │
                │  │  └────────────┘  │  │
                │  └──────────────────┘  │
                └────────────────────────┘
```

**Note on Network Isolation:** Network-level air-gapping (firewall rules, network segmentation) is configured outside of Ansible by the infrastructure team. Scout's Ansible playbooks configure the services but assume that network controls are already in place. The production k3s cluster should not have network access to the staging k3s cluster or its Kubernetes API; communication is limited to Harbor's registry endpoints (HTTPS port 443) only.

### Network Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Initial Image Pull                                       │
│                                                              │
│  Worker Node → Harbor → Docker Hub/GHCR → Harbor Cache     │
│  (needs nginx image)   (checks cache)    (downloads)        │
│                          ↓                                   │
│                       cache miss                             │
│                                                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 2. Subsequent Image Pull (same image)                       │
│                                                              │
│  Worker Node → Harbor → Returns cached image                │
│  (needs nginx image)   (cache hit)                          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Components

### Staging Node

**Purpose:** Acts as a bridge between the internet and the air-gapped cluster

**Characteristics:**
- Has internet access
- Runs its own standalone k3s server (not part of main cluster)
- Hosts Harbor container registry
- Does NOT need access to production k3s cluster API (deployments run from jump node)
- Runs on a single physical/virtual machine

**Why separate k3s?**
- Staging node provisions Harbor before main cluster exists
- Isolation from production workloads
- Independent lifecycle management
- Simpler to tear down and rebuild without affecting main cluster

### Harbor Registry

**Purpose:** Proxy cache for container images

**Features enabled:**
- **Core registry**: Image storage and proxy
- **Ingress**: HTTPS access via Traefik
- **TLS**: Self-signed certificates (generated at deployment)
- **API**: RESTful API for management operations
- **Projects**: Proxy cache projects for each upstream registry

**Features disabled:**
- **ChartMuseum**: Harbor cannot proxy Helm charts (technical limitation)
- **Trivy scanning**: Not needed for proxy cache use case (can be enabled later)
- **Notary**: Image signing not required for initial implementation

### Proxy Cache Projects

Harbor is configured with proxy cache projects for:

1. **Docker Hub** (`dockerhub-proxy`)
   - Upstream: `https://hub.docker.com`
   - Caches images like `nginx:latest`, `redis:7`, etc.
   - Transparent pull-through cache

2. **GitHub Container Registry** (`ghcr-proxy`)
   - Upstream: `https://ghcr.io`
   - Caches images like `ghcr.io/washu-tag/superset:4.1.2`
   - Used for Scout's own images

### Registry Mirror Configuration

The k3s cluster nodes are configured with `/etc/rancher/k3s/registries.yaml`:

```yaml
mirrors:
  docker.io:
    endpoint:
      - "https://staging-node.example.com/v2/dockerhub-proxy"
  ghcr.io:
    endpoint:
      - "https://staging-node.example.com/v2/ghcr-proxy"

configs:
  "staging-node.example.com":
    tls:
      insecure_skip_verify: true  # Self-signed certificate
```

**How it works:**
1. When a pod needs `nginx:latest`, k3s checks the mirror configuration
2. Instead of pulling from `docker.io`, k3s pulls from Harbor's Docker Hub proxy
3. Harbor checks its cache; if miss, pulls from Docker Hub and caches
4. Image is returned to k3s node and cached locally
5. Next pull of `nginx:latest` (on any node) hits Harbor's cache

## Deployment Order

**Critical:** Components must be deployed in strict order:

```
1. Staging K3s
   └─ Creates k3s on staging node
      └─ Provides Kubernetes API for Harbor deployment

2. Harbor
   └─ Deploys to staging k3s
      └─ Creates proxy cache projects
         └─ Ready to serve image pull requests

3. Main K3s
   └─ Installs k3s on cluster nodes
      └─ Configures registry mirrors
         └─ Points to Harbor

4. Scout Services
   └─ Deploy normally
      └─ Images pulled through Harbor
```

**Why this order?**
- Harbor needs k3s to run (Step 1 → Step 2)
- Main cluster needs Harbor to pull images (Step 2 → Step 3)
- Services need main cluster (Step 3 → Step 4)

## Configuration Variables

### Inventory Variables

The staging node is enabled via inventory configuration:

```yaml
# Set to true to enable staging node functionality
use_staging_node: false

staging:
  hosts:
    staging-node.example.com:
      ansible_host: staging-node
      ansible_python_interpreter: /usr/bin/python3
  vars:
    # Staging-specific variables (only needed if use_staging_node: true)
    staging_k3s_token: "separate-token-from-main-cluster"
    harbor_admin_password: "VaultEncryptedPassword"
    harbor_storage_size: 100Gi
    harbor_namespace: harbor

k3s_cluster:
  vars:
    # Main cluster variables...
```

### Role Defaults

The `harbor` role provides sensible defaults:

```yaml
# roles/harbor/defaults/main.yaml
harbor_version: "1.14.2"
harbor_namespace: harbor
harbor_storage_size: 100Gi
harbor_dockerhub_proxy_project: dockerhub-proxy
harbor_ghcr_proxy_project: ghcr-proxy
```

## TLS Certificate Strategy

### Initial Implementation: Self-Signed Certificates

**Generation:**
```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout harbor-tls.key \
  -out harbor-tls.crt \
  -subj "/CN=staging-node.example.com/O=Scout Harbor"
```

**Configuration:**
- Certificate stored as Kubernetes secret
- Harbor configured to use secret for TLS
- k3s configured with `insecure_skip_verify: true` for staging hostname

**Trade-offs:**
- ✅ Simple to implement
- ✅ No external dependencies
- ✅ Works in completely air-gapped environments
- ❌ Requires `insecure_skip_verify` (security concern)
- ❌ Certificate management manual

### Future Enhancement: Cert-Manager

**Recommended for production:**
- Deploy cert-manager to staging k3s
- Create self-signed ClusterIssuer or use enterprise CA
- Automatic certificate rotation
- Remove `insecure_skip_verify` configuration
- Better security posture

## Helm Chart Handling

> **Superseded:** The decision on Helm chart handling has been made. Helm runs on the Ansible control node ("jump node"), not the staging node. See [ADR-0007](adr/0007-jump-node-architecture-adr.md) for the rationale. The options below are preserved for historical context.

### Problem: Harbor Cannot Proxy Helm Charts

Harbor's proxy cache feature only supports container images (OCI artifacts), not traditional Helm charts. See [Harbor Issue #17355](https://github.com/goharbor/harbor/issues/17355).

### Solution Options (Historical)

Two approaches are under consideration:

#### Option 1: Delegate Helm Operations to Staging Node

**Strategy:**
1. Staging node has internet access
2. Staging node can pull Helm charts from public repositories
3. Staging node can access k3s cluster API (requires network exception)
4. Helm can deploy to remote clusters via kubeconfig

**Implementation:**
```yaml
# On staging node, with kubeconfig pointing to main cluster
helm install superset superset/superset \
  --kubeconfig /path/to/main-cluster-kubeconfig \
  --namespace superset \
  --create-namespace
```

**In Ansible:**
```yaml
- name: Deploy service via Helm
  hosts: staging  # Run on staging node
  environment:
    KUBECONFIG: /path/to/main-cluster-kubeconfig
  tasks:
    - name: Install Helm chart
      kubernetes.core.helm:
        name: service-name
        chart_ref: repo/chart
        # Deploys to remote cluster
```

**Pros:**
- Simpler implementation, uses existing Helm tooling
- No additional infrastructure required
- Staging node already exists for Harbor

**Cons:**
- Requires staging node to access k3s cluster API (network exception to air-gapping)
- Cluster credentials must be stored on internet-connected staging node
- If staging node is compromised, attacker gains cluster admin access

#### Option 2: Pre-Rendered Helm Charts

**Strategy:**
1. Staging node renders Helm charts to Kubernetes manifests using `helm template`
2. Manifests are transferred to k3s control plane (via Ansible or file copy)
3. Manifests are applied directly to cluster using `kubectl apply`
4. No API access required from staging node to cluster

**Implementation:**
```yaml
# On staging node: render charts to manifests
- name: Template Helm charts
  hosts: staging
  tasks:
    - name: Render Helm chart to manifest
      ansible.builtin.shell: |
        helm template {{ chart_name }} {{ chart_ref }} \
          --namespace {{ namespace }} \
          --values values.yaml \
          > /tmp/{{ chart_name }}-manifest.yaml

# Copy manifest to control plane
- name: Transfer and apply manifest
  hosts: k3s_control
  tasks:
    - name: Copy manifest from staging
      ansible.builtin.copy:
        src: /tmp/{{ chart_name }}-manifest.yaml
        dest: /tmp/{{ chart_name }}-manifest.yaml

    - name: Apply manifest
      kubernetes.core.k8s:
        src: /tmp/{{ chart_name }}-manifest.yaml
      environment:
        KUBECONFIG: /etc/rancher/k3s/k3s.yaml
```

**Pros:**
- Better security: staging node does not need k8s API access
- Maintains stricter air-gapping (no cluster credentials on staging node)
- Manifests can be reviewed/audited before application

**Cons:**
- More complex deployment process
- Loses Helm's built-in upgrade/rollback capabilities
- Requires additional orchestration in Ansible
- Chart values must be managed separately

#### Decision Criteria

The choice between these options depends on:
- Security requirements (how strict must air-gapping be?)
- Operational complexity (who will maintain the deployment process?)
- Network policy (can staging node access k8s API or not?)
- Use of Helm features (do we need rollback, history, hooks?)

## Storage Management

### Harbor Storage Requirements

**Components that require storage:**
1. **Registry**: Container image layers and manifests
2. **Database**: Harbor metadata (PostgreSQL)
3. **Redis**: Cache and job queue

**Storage provisioning:**
```yaml
# Created on staging node filesystem
Directory: /scout/persistence/harbor
Permissions: 0777 (containers can write)

# Kubernetes resources
StorageClass: harbor-storage (local storage)
PersistentVolume: harbor-pv (bound to directory above)
```

**Size estimation:**
- Depends on number of unique images
- Docker Hub proxy: 10-50GB typical
- GHCR proxy: 5-20GB for Scout images
- Recommend starting with 100GB, monitor usage

### Cache Retention

Harbor creates default 7-day retention policy for proxy projects:
- Images not pulled for 7 days are purged
- Reduces storage requirements
- Automatically re-cached on next pull
- Configurable via Harbor UI or API

## Monitoring and Operations

### Health Checks

**Harbor API health endpoint:**
```bash
curl -k https://staging-node.example.com/api/v2.0/health
```

**Kubernetes pod status:**
```bash
kubectl -n harbor get pods
```

### Cache Performance

**View cache statistics in Harbor UI:**
1. Navigate to Projects → dockerhub-proxy
2. Check "Repositories" to see cached images
3. View pull counts and sizes

**API query for cache metrics:**
```bash
curl -u admin:password -k \
  https://staging-node.example.com/api/v2.0/projects/dockerhub-proxy/summary
```

### Common Operations

**Clear cache for a project:**
```bash
# Via UI: Projects → dockerhub-proxy → Repositories → Delete
# Via API: DELETE /api/v2.0/projects/dockerhub-proxy/repositories/{repo_name}
```

**Add new registry proxy:**
```bash
# Create registry endpoint
curl -X POST -u admin:password -k \
  https://staging-node.example.com/api/v2.0/registries \
  -H "Content-Type: application/json" \
  -d '{"name": "quay", "type": "quay", "url": "https://quay.io"}'

# Create proxy project
curl -X POST -u admin:password -k \
  https://staging-node.example.com/api/v2.0/projects \
  -H "Content-Type: application/json" \
  -d '{"project_name": "quay-proxy", "registry_id": <id>}'
```

## Security Considerations

### Attack Surface

**Exposed services:**
- Harbor web UI (HTTPS on staging node)
- Harbor registry API (HTTPS on staging node)
- k3s API (both staging and main cluster)

**Mitigation:**
- Harbor admin password vault-encrypted
- TLS for all communications
- Staging node in isolated network segment
- Main cluster cannot reach internet (defense in depth)

### Authentication and Authorization

**Harbor:**
- Admin credentials required for project creation/management
- Proxy projects configured as public (no auth required for pulls)
- Consider adding authentication for production deployments

**Registry mirrors:**
- k3s trusts staging node (via `insecure_skip_verify` or CA certificate)
- No credentials currently configured for image pulls
- Can add authentication via pull secrets if needed

### Network Segmentation

**Note:** Network-level firewall configuration is managed by the infrastructure team outside of Scout's Ansible playbooks. The following describes the expected network topology and access controls that should be in place.

**Recommended network zones:**
```
Internet Zone (unrestricted)
    ↓
DMZ Zone (limited egress)
    ← Staging Node
    ↓
Production Zone (no internet)
    ← K3s Cluster
```

**Expected firewall rules:**
- Staging → Internet: HTTPS (443) for Docker Hub, GHCR
- K3s Cluster → Staging: HTTPS (443) for Harbor registry endpoints only
- K3s Cluster → Staging: **No access** to staging k3s API (6443)
- K3s Cluster → Internet: BLOCKED
- Staging → K3s Cluster: **No access required** (Helm runs from jump node, not staging)

## Troubleshooting

### Image Pull Failures

**Symptom:** Pod stuck in ImagePullBackOff

**Diagnosis:**
```bash
# Check pod events
kubectl describe pod <pod-name>

# Check node can reach Harbor
ssh worker-node
curl -k https://staging-node.example.com/v2/

# Check Harbor has the image
curl -k https://staging-node.example.com/v2/dockerhub-proxy/<image>/tags/list
```

**Common causes:**
1. Registry mirror misconfigured in `/etc/rancher/k3s/registries.yaml`
2. Harbor down or unreachable
3. Upstream registry (Docker Hub) rate limiting
4. Image name typo

### Harbor Service Unavailable

**Symptom:** Cannot access Harbor UI or API

**Diagnosis:**
```bash
# Check Harbor pods
kubectl -n harbor get pods

# Check Harbor logs
kubectl -n harbor logs -l app=harbor

# Check ingress
kubectl -n harbor get ingress

# Check Traefik
kubectl -n kube-system logs -l app.kubernetes.io/name=traefik
```

**Common causes:**
1. Storage full (check `/scout/persistence/harbor`)
2. Database issues (PostgreSQL pod unhealthy)
3. Ingress misconfiguration
4. Certificate issues

### Registry Mirror Not Working

**Symptom:** Images still being pulled from Docker Hub directly

**Diagnosis:**
```bash
# Check registries.yaml exists
ssh k3s-node
cat /etc/rancher/k3s/registries.yaml

# Check k3s service was restarted after config change
systemctl status k3s
systemctl status k3s-agent

# Check containerd config
crictl info | jq .config.registry
```

**Fix:**
```bash
# Restart k3s services
systemctl restart k3s
systemctl restart k3s-agent

# Verify registry config loaded
crictl info | jq .config.registry.mirrors
```

## Limitations

### Current Limitations

1. **No Helm chart proxy** - Harbor cannot cache Helm charts; Helm runs from jump node instead
2. **Self-signed certificates** - Requires `insecure_skip_verify`, should upgrade to cert-manager
3. **Single staging node** - No high availability, single point of failure
4. **No pull authentication** - Harbor proxy projects are public
5. **Manual proxy project creation** - Could be more automated

### Technical Constraints

1. **Harbor proxy cache limitations:**
   - Only supports OCI artifacts (container images)
   - Cannot proxy traditional Helm chart repositories
   - 7-day default retention (configurable)

2. **k3s registry mirror limitations:**
   - Registry config requires service restart
   - Certificate trust must be configured per-registry
   - Mirrors apply cluster-wide, not per-namespace

3. **Network requirements:**
   - Staging node must have reliable internet access
   - Latency between cluster and staging node affects image pull speed
   - Bandwidth constraints affect initial cache population

## References

- [Harbor Documentation](https://goharbor.io/docs/)
- [Harbor Proxy Cache Configuration](https://goharbor.io/docs/2.1.0/administration/configure-proxy-cache/)
- [k3s Private Registry Configuration](https://docs.k3s.io/installation/private-registry)
- [Scout Ansible Roles Documentation](./ansible_roles.md)
- [Molecule Testing Documentation](./molecule_ansible_testing.md)
