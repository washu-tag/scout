# Air-Gapped Deployment

This guide covers deploying Scout in air-gapped environments where production nodes do not have internet access.

## Overview

Scout supports air-gapped deployments through a staging node architecture. Ansible automatically deploys a K3s cluster and Harbor registry on the staging node, which acts as a proxy between the internet and your production cluster.

### Architecture

```
┌──────────────┐         ┌──────────────────────────────┐
│   Internet   │────────▶│  Staging Node                │
└──────────────┘         │  - K3s cluster (auto-deploy) │
                         │  - Harbor registry proxy     │
                         └──────┬───────────────────────┘
                                │
                         Air Gap│ (registry mirrors)
                                │
                         ┌──────▼───────────────────────┐
                         │  Production K3s Cluster      │
                         │  - No internet access        │
                         │  - Pulls images via Harbor   │
                         └──────────────────────────────┘
```

**How it works:**
- Harbor pull-through proxy automatically caches container images from the internet
- Production nodes pull images through Harbor without needing internet access
- K3s artifacts are downloaded to the Ansible control node and distributed to air-gapped production nodes
- SELinux packages are downloaded via a Kubernetes Job on the staging cluster

## Requirements

### Operating System

**Critical:** Production K3s nodes must run **Rocky Linux 9** (or compatible RHEL 9-based distribution).

This requirement exists because air-gapped installations download SELinux packages (`k3s-selinux` and `container-selinux`) from Rancher's repository using a Kubernetes Job that runs Rocky Linux 9 containers. The downloaded packages must match the production node OS to ensure compatibility.

### Staging Node

- Internet access for downloading artifacts and container images
- Separate physical or virtual machine from production cluster
- Sufficient storage for Harbor registry cache (recommend 100Gi+)
- SSH access from Ansible control node
- Rocky Linux 9 (recommended for consistency, but not strictly required)

### Production Nodes

- Rocky Linux 9 (required)
- No internet access needed
- Network connectivity to staging node Harbor registry
- SSH access from Ansible control node

### Ansible Control Node

- Network access to both staging and production K8s API servers (port 6443)
- `kubectl` command-line tool installed
- Ansible `kubernetes.core` collection installed
- Internet access (for downloading Helm charts and k3s artifacts to control node)

### Network Connectivity

- Production nodes → Staging Harbor (HTTPS, typically port 443 but configurable in the inventory)
- Ansible control → Staging K8s API (port 6443)
- Ansible control → Production K8s API (port 6443)
- Ansible control → All nodes (SSH, port 22)

(staging-host-configuration)=
## Staging Host Configuration

### Adding Staging to Inventory

Add a `staging` group to your `inventory.yaml`:

```yaml
staging:
  hosts:
    staging.example.edu:
      ansible_host: staging
      ansible_python_interpreter: /usr/bin/python3
  vars:
    # K3s cluster join token for staging cluster
    staging_k3s_token: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...encrypted token...

    # Harbor admin password
    harbor_admin_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...encrypted password...

    # Storage size for Harbor registry cache
    harbor_storage_size: 100Gi

    # Local directory on staging node for Harbor data
    harbor_dir: /scout/persistence/harbor
```

### Required Variables

| Variable | Description |
|----------|-------------|
| `staging_k3s_token` | Cluster join token for the staging K3s cluster (separate from production `k3s_token`) |
| `harbor_admin_password` | Admin password for Harbor web UI and API |
| `harbor_storage_size` | Persistent volume size for cached images (recommend 100Gi minimum) |
| `harbor_dir` | Local path on staging node where Harbor data is stored |

### Generating Staging Credentials

Generate and encrypt credentials using Ansible Vault:

```bash
# Generate staging K3s token
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh --name 'staging_k3s_token'

# Generate Harbor admin password
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh --name 'harbor_admin_password'
```

See {ref}`configuring-secrets` for more details on Ansible Vault.

## Air-Gapped Configuration Variables

### Enabling Air-Gapped Mode

Set the global `air_gapped` flag in your inventory:

```yaml
all:
  vars:
    air_gapped: true
```

### K3s Air-Gapped Variables

Configure these variables in the `k3s_cluster` vars section or globally in `all` vars:

```yaml
k3s_cluster:
  vars:
    # Enable air-gapped installation mode (default: false)
    air_gapped: true

    # Timeout for downloading k3s artifacts to control node in seconds (default: 300)
    k3s_artifact_download_timeout: 300

    # SELinux package installation (default: auto-detect based on target node SELinux status)
    # Set to true/false to override auto-detection
    # k3s_selinux_enabled: true

    # Rancher repository channel for SELinux packages (default: stable)
    # Options: stable, testing, latest
    k3s_selinux_channel: stable
```

### Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `air_gapped` | `false` | Enable air-gapped installation mode |
| `k3s_artifact_download_timeout` | `300` | Timeout in seconds for downloading k3s binary and install script to Ansible control node |
| `k3s_selinux_enabled` | auto-detect | Install SELinux packages (`k3s-selinux`, `container-selinux`). Set to `true`/`false` to override auto-detection based on target host SELinux status |
| `k3s_selinux_channel` | `stable` | Rancher repository channel for SELinux packages. Options: `stable`, `testing`, `latest` |
| `k3s_selinux_rpm_site` | `rpm.rancher.io` | Rancher RPM repository site (rarely needs changing) |

## Deployment Steps

Follow these steps to deploy Scout in air-gapped mode:

### 1. Enable Air-Gapped Mode

Set `air_gapped: true` in your inventory file:

```yaml
all:
  vars:
    air_gapped: true
```

### 2. Configure Staging Host

Add the staging host and credentials to your inventory (see {ref}`staging-host-configuration` above).

### 3. Configure Production Cluster

Define your production K3s cluster nodes as usual in the `server`, `workers`, and optionally `gpu_workers` groups. Ensure all nodes run Rocky Linux 9.

```yaml
server:
  hosts:
    prod-server.example.edu:
      ansible_host: prod-server

workers:
  hosts:
    prod-worker-1.example.edu:
      ansible_host: worker-1
    prod-worker-2.example.edu:
      ansible_host: worker-2
```

### 4. Deploy

Deploy Scout components normally:

```bash
ansible-playbook -i inventory.yaml playbooks/main.yaml

# Or use the Makefile
make all
```

**What happens:**
- `staging` play installs a single-node K3s cluster on the staging host (online mode), configures Traefik, and deploys Harbor via Helm with pull-through proxy configured for Docker Hub, Quay.io, and GitHub Container Registry
- `k3s` play
  - Downloads K3s artifacts (binary, install script) to Ansible control node
  - Downloads SELinux packages via Kubernetes Job on staging cluster
  - Distributes artifacts to production nodes that lack internet access
  - Installs K3s with Harbor registry mirrors configured so production nodes can pull container images through Harbor
- Other Scout plays
  - Helm charts are deployed from Ansible control node (charts are bundled in the Scout repository)
  - Container images are pulled by production nodes through Harbor
  - Harbor automatically caches images from upstream registries on first pull

## How It Works

### Harbor Pull-Through Proxy

Harbor acts as a transparent caching proxy for container registries:

1. Production pod requests an image (e.g., `docker.io/postgres:15`)
2. K3s containerd is configured to rewrite requests to Harbor (e.g., `staging.example.edu/dockerhub-proxy/postgres:15`)
3. Harbor checks its cache:
   - **Cache hit:** Returns cached image immediately
   - **Cache miss:** Downloads from internet, caches, returns to requester
4. Subsequent requests for the same image are served from Harbor cache

**Supported registries:**
- Docker Hub (`docker.io`)
- GitHub Container Registry (`ghcr.io`)
- Quay.io (`quay.io`)
- K8ssandra Container Registry (`cr.k8ssandra.io`)
- Kubernetes Registry (`registry.k8s.io`)
- Elastic Docker Registry (`docker.elastic.co`)
- NVIDIA GPU Cloud (`nvcr.io`)
- Apache Superset (`apachesuperset.docker.scarf.sh`)

### K3s Artifact Distribution

In air-gapped mode, k3s installation artifacts are handled differently:

1. **Download phase** (on Ansible control node):
   - K3s binary downloaded from GitHub releases
   - Install script downloaded from `get.k3s.io`
   - SELinux RPMs downloaded via Kubernetes Job on staging cluster

2. **Distribution phase:**
   - Artifacts copied from control node to production nodes via SSH
   - Install script run with `INSTALL_K3S_SKIP_DOWNLOAD=true`
   - SELinux packages installed via `dnf`

### SELinux Package Download

SELinux packages are downloaded using a unique Kubernetes Job approach:

1. Ansible creates a Job on the staging cluster
2. Job runs a Rocky Linux 9 init container that:
   - Configures Rancher k3s yum repository
   - Downloads `k3s-selinux` and `container-selinux` with all dependencies
   - Saves RPMs to a shared volume
3. Main container keeps the pod running
4. Ansible extracts RPMs using `kubectl cp` from the pod
5. RPMs are fetched to control node and distributed to production nodes

This approach ensures correct package versions for Rocky Linux 9 without requiring yum repositories on air-gapped nodes.

## Limitations

### Version Upgrades

When upgrading k3s or other components:
1. Test the upgrade in your staging environment first
2. Use `-e` flag to override versions temporarily (see {ref}`testing-upgrades`)
3. Update `group_vars/all/versions.yaml` after validating the upgrade
4. Deploy to production

### Network Isolation

Air-gapped mode prevents production nodes from accessing the internet, but:
- Production nodes still need access to staging Harbor
- Ansible control node needs access to K8s APIs
- This is not a completely isolated environment (no external network access)

### Operating System Support

Air-gapped installations only support **Rocky Linux 9** for production nodes due to SELinux package requirements. The staging node can run other distributions, but Rocky Linux 9 is recommended for consistency.

## Additional information:
- [Creating the Ansible Inventory File](inventory.md)
- [K3s Air-Gapped Installation Docs](https://docs.k3s.io/installation/airgap)
- [Harbor Documentation](https://goharbor.io/docs/latest/)
